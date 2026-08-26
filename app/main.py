
from typing import Any, Dict, List

import streamlit as st

from backend.core import run_llm

# Document -> source URL, de-nulled. `context_docs or []` guards against None,
# and getattr(...) or {} guards against objects without metadata.
# helper function to format the sources of the retrieved documents
def _format_sources(context_docs: List[Any]) -> List[str]:
    return [
        str(meta.get("source", "Unknown"))
        for doc in (context_docs or [])
        if(meta := (getattr(doc, "metadata", None) or {})) is not None
    ]


# Must be the first Streamlit call in the script, otherwise Streamlit raises.
st.set_page_config(page_title="Strudel Documentation Assistant", page_icon=":books:", layout="centered")
st.title("Strudel Documentation Assistant :books:")

with st.sidebar:
    st.subheader("Session")
    if st.button("Clear Chat", use_container_width=True):
        # Drop the history, then force an immediate rerun so the seed message
        # below is recreated on the spot instead of on the next interaction.
        st.session_state.pop("messages", None)
        st.rerun()


# Runs only on the first load (and right after "Clear Chat"): session_state
# persists across reruns, so this seed is not re-applied on every keystroke.
if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant", 
            "content": "Ask me anything about Strudel docs. I'll retreive relevant context and cite sources.",
            "sources": [], # list of sources for the assistant's response
        }
    ]

# Replay the whole conversation on every rerun - this is what makes the chat
# look persistent even though the script restarts from scratch each time.
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            with st.expander("Sources"):
                for s in msg["sources"]:
                    st.markdown(f"- {s}") # "-" is used to create a bullet point in markdown

# Returns None while the user has not submitted anything this run.
prompt = st.chat_input("Ask me anything about Strudel docs...")
if prompt:
    # Store first, then render: the append is what survives the next rerun,
    # the markdown call only paints the current one.
    st.session_state.messages.append({"role": "user", "content": prompt, "sources": []})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            # Blocking call: retrieval + LLM happen here, the spinner is the only
            # feedback. Streaming the answer token by token is the natural upgrade.
            with st.spinner("Retreiving docs and generating response..."):
                result: Dict[str, Any] = run_llm(prompt)
                answer = str(result.get("answer", "")).strip() or "(No answer returned.)"
                sources = _format_sources(result.get("context", []))

            st.markdown(answer)
            if sources:
                with st.expander("Sources"):
                    for s in sources:
                        st.markdown(f"- {s}")

            st.session_state.messages.append(
                {"role": "assistant", "content": answer, "sources": sources}
            )
            
        # Missing API key, empty Pinecone index, rate limit... surface it in the
        # UI instead of leaving the user with a blank bubble.
        except Exception as e:
            st.error(f"Error: {str(e)}")
            st.exception(e)