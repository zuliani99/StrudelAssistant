"""
FastAPI server for the Strudel Documentation Assistant.

Serves a single static page (app/web) and one JSON endpoint that wraps the
existing RAG pipeline in backend.core:

    GET  /            -> the app shell
    POST /api/chat    -> {answer_html, answer_md, sources}

Run it with:

    uv run python app/server.py

Launching by file path matters: Python puts this file's directory (app/) on
sys.path[0], which is how `backend.core` and its own `from logger import ...`
resolve. The APP_DIR insert below makes that hold for `uvicorn server:app` too.
"""

from __future__ import annotations

import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from markdown_it import MarkdownIt  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

WEB_DIR = APP_DIR / "web"

# html=False: the answer is model output, so raw HTML in it is escaped rather
# than rendered. Tables stay enabled because API reference answers use them.
_md = MarkdownIt("commonmark", {"html": False, "breaks": False}).enable(
    ["table", "strikethrough"]
)

# backend.core does real work at import time (builds the BM25 index from
# data/chunks.json and opens the Pinecone handle), so it is loaded lazily and
# only once. The server can then boot instantly and surface a configuration
# problem as a readable message in the UI instead of a stack trace at startup.
_run_llm: Optional[Callable[..., Dict[str, Any]]] = None
_load_lock = threading.Lock()


def _get_run_llm() -> Callable[..., Dict[str, Any]]:
    global _run_llm
    if _run_llm is None:
        with _load_lock:
            if _run_llm is None:
                from backend.core import run_llm

                _run_llm = run_llm
    return _run_llm


def _prewarm() -> None:
    """Build the retriever in the background so the first question isn't slow."""
    try:
        _get_run_llm()
    except Exception as exc:  # surfaced properly on the first real request
        print(f"[server] retriever not ready: {type(exc).__name__}: {exc}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    threading.Thread(target=_prewarm, daemon=True).start()
    yield


app = FastAPI(title="Strudel Documentation Assistant", lifespan=lifespan)


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    # Prior turns, oldest first, NOT including `message` itself - this is what
    # lets the agent resolve follow-ups like "now add reverb to that".
    history: List[ChatTurn] = Field(default_factory=list)


def _format_sources(context_docs: List[Any]) -> List[str]:
    """Document -> source URL, de-duplicated, first-seen order preserved.

    The retriever returns one document per chunk, so a single page routinely
    shows up several times; the UI only wants the distinct pages.
    """
    seen: set[str] = set()
    ordered: List[str] = []
    for doc in context_docs or []:
        meta = getattr(doc, "metadata", None) or {}
        source = str(meta.get("source", "")).strip()
        if source and source not in seen:
            seen.add(source)
            ordered.append(source)
    return ordered


def _explain(exc: Exception) -> str:
    """Turn an exception into something the reader can act on."""
    text = f"{exc}".lower()
    if "openai_api_key" in text or "api key" in text:
        return "No valid OpenAI API key. Add OPENAI_API_KEY to .env and restart the server."
    if "pinecone" in text:
        return (
            "Couldn't reach the Pinecone index. Check PINECONE_API_KEY in .env, and run "
            "`uv run python app/ingestion.py` if the index is empty."
        )
    if "rate limit" in text or "429" in text:
        return "The model API is rate limiting these requests. Wait a moment, then ask again."
    return f"{type(exc).__name__}: {exc}"


@app.post("/api/chat")
def chat(request: ChatRequest) -> Any:
    # Defined with `def`, not `async def`: run_llm blocks on retrieval and the
    # model call, so FastAPI runs it in a worker thread and the event loop stays
    # free to serve everything else.
    question = request.message.strip()
    if not question:
        return JSONResponse({"error": "Type a question first."}, status_code=400)

    try:
        run_llm = _get_run_llm()
        history = [
            {"role": turn.role, "content": turn.content}
            for turn in request.history
            if turn.role in ("user", "assistant")
        ]
        result: Dict[str, Any] = run_llm(question, chat_history=history)
    except Exception as exc:
        return JSONResponse({"error": _explain(exc)}, status_code=500)

    answer = str(result.get("answer", "")).strip() or "No answer came back."
    return {
        "answer_html": _md.render(answer),
        "answer_md": answer,
        "sources": _format_sources(result.get("context", [])),
    }


@app.get("/")
def index() -> FileResponse:
    # no-store: this is a dev server, and a cached shell means edits to the
    # page silently don't show up on reload.
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
