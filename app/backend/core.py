import json
import os
from pathlib import Path
from typing import Any, Dict, List, cast

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.messages import ToolMessage
from langchain.tools import tool
from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_pinecone import PineconeVectorStore
from langchain_openai import OpenAIEmbeddings

from logger import log_warning

load_dotenv()

# How many chunks each retriever pulls before the ensemble merges/dedupes them.
RETRIEVAL_K = 10

# MUST match ingestion.py exactly: query vectors and stored vectors have to come
# from the same model or similarity search is meaningless.
# Initialize embeddings (same as ingestion.py)
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

# Read-only handle on the index that ingestion.py populated.
#Initialize vector store
vectorstore = PineconeVectorStore(
    index_name="strudel-doc-index", embedding=embeddings
)
# Provider-agnostic factory: change the string to switch model or vendor.
# Initialize chat model
model = init_chat_model("gpt-4o-mini", model_provider="openai")

# Chunk dump written by ingestion.py (app/ -> project root -> data/chunks.json).
CHUNKS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "chunks.json"


def _load_local_chunks() -> List[Document]:
    if not CHUNKS_PATH.exists():
        return []
    raw = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    return [Document(page_content=item["page_content"], metadata=item["metadata"]) for item in raw]


def _build_retriever():
    """Hybrid retriever: BM25 (lexical) + Pinecone (semantic), merged by
    EnsembleRetriever (reciprocal rank fusion). BM25 catches exact
    keyword/function-name matches ("lpf", "gain") that dense embeddings alone
    sometimes rank low; the vector side catches paraphrases/synonyms BM25 can't
    see. Falls back to vector-only if ingestion hasn't been run yet (no local
    chunk dump to build BM25 from).
    """
    vector_retriever = vectorstore.as_retriever(search_kwargs={"k": RETRIEVAL_K})

    local_chunks = _load_local_chunks()
    if not local_chunks:
        log_warning(
            f"No local chunk dump found at {CHUNKS_PATH}: falling back to vector-only retrieval. "
            "Run `uv run python app/ingestion.py` to enable hybrid BM25 + vector search."
        )
        return vector_retriever

    bm25_retriever = BM25Retriever.from_documents(local_chunks)
    bm25_retriever.k = RETRIEVAL_K

    return EnsembleRetriever(retrievers=[bm25_retriever, vector_retriever], weights=[0.5, 0.5])


retriever = _build_retriever()


# response_format="content_and_artifact" makes the tool return a 2-tuple:
#   [0] content  -> the string the MODEL sees (must be text)
#   [1] artifact -> an arbitrary Python object the model never sees, stored on
#                   the ToolMessage for the application to use
# That is how the raw Document objects survive the round-trip and reach the UI,
# instead of being flattened into the prompt string.
@tool(response_format="content_and_artifact")
def retrieve_context(query: str):
    """Retrieve relevant documentation to help answer user queries about Strudel REPL."""
    # Hybrid BM25 + vector retrieval (see _build_retriever above), k=RETRIEVAL_K
    # actually applied via search_kwargs/bm25_retriever.k, not passed to invoke().
    retrieved_docs = retriever.invoke(query)

    # The source URL is embedded in the text the model reads, which is what makes
    # the "always cite the sources" instruction in the system prompt satisfiable.
    # Serialize documents for the model
    serialized = "\n\n".join(
        (f"Source: {doc.metadata.get('source', 'Unknown')}\n\nContent: {doc.page_content}")
        for doc in retrieved_docs
    )
    
    # Return both serialized content and raw documents
    return serialized, retrieved_docs


def run_llm(query: str) -> Dict[str, Any]:
    """
    Run the RAG pipeline to answer a query using retrieved documentation.
    
    Args:
        query: The user's question
        
    Returns:
        Dictionary containing:
            - answer: The generated answer
            - context: List of retrieved documents
    """
    # Create the agent with retrieval tool
    system_prompt = (
        "You are a helpful AI assistant that answers questions about Strudel REPL documentation. "
        "You have access to a tool that retrieves relevant documentation. "
        "Use the tool to find relevant information before answering questions. "
        "Always cite the sources you use in your answers. "
        "If you cannot find the answer in the retrieved documentation, say so."
    )
    
    # A fresh agent per call. In production build it once at module level - this
    # rebuilds the graph on every request.
    agent = create_agent(model, tools=[retrieve_context], system_prompt=system_prompt)
    
    # No history is passed: this app is stateless, every question stands alone.
    # Build messages list
    messages = [{"role": "user", "content": query}]
    
    # Invoke the agent
    response = agent.invoke(cast(Any, {"messages": messages}))
    
    # The loop has ended, so the last message is the AI's plain-text answer.
    # Extract the answer from the last AI message
    answer = response["messages"][-1].content
    
    # Walk the transcript and collect every retrieved Document. There may be
    # several ToolMessages if the agent searched more than once.
    # Extract context documents from ToolMessage artifacts
    context_docs = []
    for message in response["messages"]:
        # Check if this is a ToolMessage with artifact
        if isinstance(message, ToolMessage) and hasattr(message, "artifact"):
            # The artifact should contain the list of Document objects
            if isinstance(message.artifact, list):
                context_docs.extend(message.artifact)
    
    return {
        "answer": answer,
        "context": context_docs
    }

if __name__ == '__main__':
    result = run_llm(query="what are deep agents?")
    print(result)