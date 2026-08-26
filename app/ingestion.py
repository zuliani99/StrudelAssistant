"""
Lesson 6 / ingestion - Crawl python.langchain.com and index it into Pinecone.

Production-shaped ingestion pipeline:
    CRAWL   TavilyCrawl over the docs site      -> raw markdown per page
    WRAP    each page                           -> Document(+ source URL metadata)
    SPLIT   RecursiveCharacterTextSplitter      -> 4000-char chunks, 200 overlap
    INDEX   asyncio.gather over batches of 500  -> concurrent upserts to Pinecone

Long-running and paid (crawling + embedding thousands of chunks). Run once.
Python puts this file's own directory on sys.path[0], so the sibling `logger`
import resolves from any working directory:
    uv run python app/ingestion.py
"""

import asyncio
import hashlib
import json
import os
import ssl
from pathlib import Path
from typing import Any, Dict, List


import certifi
from dotenv import load_dotenv

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from langchain_tavily import TavilyCrawl, TavilyExtract, TavilyMap

# Sibling module, imported absolutely -> this file must be run from its own
# folder (or that folder must be on PYTHONPATH).
from logger import (
    Colors, log_info,
    log_success, log_warning,
    log_error, log_header
)

# Project root (this file lives in app/), used both for the local chunk dump
# that core.py reads for BM25 and to keep the path stable regardless of cwd.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CHUNKS_PATH = DATA_DIR / "chunks.json"

load_dotenv()  # Load environment variables from .env file


# macOS ships a Python without the system trust store wired up, so crawling
# HTTPS pages fails with SSLCertVerificationError. Pointing both requests and
# the stdlib at certifi's CA bundle fixes it for every library in the process.
# Configure SSL context to use certifi certificates
ssl_context = ssl.create_default_context(cafile=certifi.where())
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()


# text-embedding-3-small: 1536 dims, ~5x cheaper than ada-002 and better on
# retrieval benchmarks. The Pinecone index must be created with dimension 1536.
embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    show_progress_bar=True,
    chunk_size=50,   # texts per embedding HTTP request (batching, not text length)
    retry_min_seconds=10 # removing it the error 429 appears, so we set it to 10 seconds to avoid hitting the rate limit too quickly.
)

vectorStore = PineconeVectorStore(index_name="strudel-doc-index", embedding=embeddings)

'''
The difference between Chroma and Pinecone is that Chroma is a local vector store that stores embeddings on disk,
while Pinecone is a managed vector database service that provides scalable and efficient storage and retrieval of embeddings in the cloud.
'''

# Three complementary Tavily tools:
#   TavilyExtract - pull clean text out of a KNOWN list of URLs
#   TavilyMap     - discover the URL graph of a site without downloading content
#   TavilyCrawl   - discover AND extract in one call (what main() actually uses)
tavily_extract = TavilyExtract()
tavily_map = TavilyMap(max_depth=5, max_breath=20, max_pages=1000)
tavily_crawl = TavilyCrawl()



async def main():
    """Main async function to orchestrate the entire process."""
    log_header("DOCUMENTATION INGESTION PIPELINE")

    log_info(
        "TavilyCrawl: Starting to Crawl dicumentation from https://strudel.cc/workshop/getting-started/", 
        color=Colors.PURPLE
    )

    # One call fans out over the whole docs site and returns extracted content.
    # Crawl the documentation site.
    # strudel.cc's left sidebar nav (workshop/learn/recipes) is rendered on
    # every page, so all doc pages are reachable within 1-2 hops from
    # getting-started; max_depth=20 was massively overkill for that reason.
    # select_paths/exclude_paths are deterministic path filters (checked
    # against the site's real nav: /workshop/*, /learn/*, /recipes/* are the
    # actual doc sections; /blog/*, /intro/showcase* and /de/* are blog posts,
    # a community showcase, and a duplicate German translation - all noise
    # for a documentation RAG corpus). "instructions" is only a soft semantic
    # hint on top of that, not a hard filter, so it's kept short and specific
    # rather than the previous "content strudel documentation" (a keyword
    # jumble, not an actual instruction).
    res = tavily_crawl.invoke({
        "url": "https://strudel.cc/workshop/getting-started/", # the URL of the documentation site to crawl
        "max_depth": 3, # nav is on every page, so 1-2 hops covers the whole site; kept a small buffer
        "extract_depth": "advanced", # treat more data as relevant for extraction, including code snippets, tables, and structured data
        "select_paths": ["/workshop/.*", "/learn/.*", "/recipes/.*"],
        "exclude_paths": ["/de/.*", "/blog/.*", "/intro/showcase.*"],
        "instructions": (
            "Strudel syntax, functions, audio effects, samples, synths, "
            "mini-notation and MIDI/OSC reference and tutorial documentation"
        ),
    })

    # all_docs = res["results"]
    # metadata["source"] is the whole point: it survives chunking and ends up in
    # the retrieved Document, which is how the UI can cite the page it came from.
    # Pages with no extractable content are skipped rather than indexed empty.
    all_docs = [
        Document(page_content=result["raw_content"], metadata={"source": result["url"]})
        for result in res["results"]
        if result.get("raw_content")
    ]
    # all_docs contains the crawled documents, each represented as a Document object with its content and source URL.

    log_success(
        f"TavilyCrawl: Successfully crawled {len(all_docs)} documents from the documentation site.",
    )

    # Split documents into chunks
    log_header("DOCUMENT CHUNKING PHASE")
    log_info(f"Text Splitter: Processing {len(all_docs)} documents with 4000 chunk size and 200 overlap.", color=Colors.PURPLE)

    # "Recursive" = try to split on paragraph breaks first, then lines, then
    # words, then characters - so code blocks and sections stay intact where
    # possible. Always prefer it over CharacterTextSplitter for real documents.
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=4000,
        chunk_overlap=200   # carry 200 chars across the cut to preserve context
    )
    splitted_docs = text_splitter.split_documents(all_docs)
    log_success(f"Text Splitter: Successfully split {len(all_docs)} documents into {len(splitted_docs)} chunks.")

    # Deterministic ids (hash of source + content) so re-running ingestion on
    # unchanged pages UPDATES the same Pinecone vectors instead of duplicating
    # them; only chunks whose text actually changed get a new id/vector.
    chunk_ids = [_chunk_id(doc) for doc in splitted_docs]

    # Dumped to disk so core.py can build a BM25 (lexical) retriever without
    # re-crawling: hybrid BM25 + vector search catches exact function/keyword
    # matches ("lpf", "gain") that dense embeddings alone sometimes miss.
    _save_chunks(splitted_docs, chunk_ids)

    # Process documents in batches asynchronously
    # Batched + concurrent, otherwise indexing thousands of chunks one HTTP call
    # at a time takes hours.
    await index_documents_async(splitted_docs, chunk_ids, batch_size=500) # depending on the size of the documents, you may want to adjust the batch size for optimal performance.

    log_header("PIPELINE COMPLETE")
    log_success("🎉 Documentation ingestion pipeline finished successfully!")
    log_info("Summary:", Colors.BOLD)
    log_info(f"   • Documents extracted: {len(all_docs)}")
    log_info(f"   • Chunks created: {len(splitted_docs)}")




def _chunk_id(doc: Document) -> str:
    """Content-addressed id: same source+text always hashes to the same id, so
    a re-run upserts (overwrites) the existing vector instead of adding a
    duplicate. A chunk whose text changed gets a new id, which is the
    intended/acceptable tradeoff of this simple scheme (the stale vector under
    the old id is left orphaned in the index rather than explicitly deleted).
    """
    source = doc.metadata.get("source", "unknown")
    return hashlib.sha256(f"{source}::{doc.page_content}".encode("utf-8")).hexdigest()


def _save_chunks(documents: List[Document], ids: List[str]) -> None:
    """Persist the split chunks (content + metadata + id) to data/chunks.json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = [
        {"id": doc_id, "page_content": doc.page_content, "metadata": doc.metadata}
        for doc, doc_id in zip(documents, ids)
    ]
    CHUNKS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log_success(f"Chunk dump: Saved {len(payload)} chunks to {CHUNKS_PATH} (used by core.py for BM25 retrieval).")


async def index_documents_async(documents: List[Document], ids: List[str], batch_size: int = 50) -> None:
    """Process documents in batches asynchronously"""
    log_header("VECTOR STORAGE PHASE")
    log_info(
        f"VectorStore Indexing: Preparing to add {len(documents)} dicuments to vector store",
        color=Colors.DARKCYAN
    )

    # Slice the corpus into fixed-size batches; each batch is one upsert request.
    # ids are sliced in lockstep so each chunk keeps the same id used in chunks.json.
    # Create bathces
    batches = [
        documents[i : i + batch_size] for i in range(0, len(documents), batch_size)
    ]
    id_batches = [
        ids[i : i + batch_size] for i in range(0, len(ids), batch_size)
    ]

    log_info(f"VectorStore Indexing: Split into {len(batches)} batches of size {batch_size} for processing.")

    # Cap concurrent upserts so a large corpus doesn't fire hundreds of batches
    # at the embeddings API at once and trip the provider's rate limit.
    semaphore = asyncio.Semaphore(5)

    # Process all batches concurrently (bounded by the semaphore above)
    async def add_batch(batch: List[Document], batch_ids: List[str], batch_number: int) -> None:
        async with semaphore:
            try:
                # aadd_documents = embed + upsert, awaited so the event loop can run
                # the other batches while this one waits on the network.
                # Explicit ids -> Pinecone upserts by id (update-in-place) instead
                # of always inserting a fresh vector with a random uuid.
                await vectorStore.aadd_documents(batch, ids=batch_ids)
                log_success(f"VectorStore Indexing: Successfully added batch {batch_number + 1}/{len(batches)} to vector store.")
            except Exception as e:
                log_error(f"VectorStore Indexing: Failed to add batch {batch_number + 1}/{len(batches)}. Error: {str(e)}")

    # return_exceptions=True -> one failing batch does not cancel the others;
    # failures come back as Exception objects and are counted below.
    tasks = [add_batch(batch, id_batches[i], i) for i, batch in enumerate(batches)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Count successful batches
    successful_batches = sum(1 for result in results if not isinstance(result, Exception))

    if successful_batches == len(batches):
        log_success(
            f"VectorStore Indexing: Successfully added all batches to vector store."
        )
    else:
        log_warning(
            f"VectorStore Indexing: Added {successful_batches}/{len(batches)} batches successfully. Some batches failed."
        )

if __name__ == "__main__":
    asyncio.run(main())