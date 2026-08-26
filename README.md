# StrudelAssistant

An AI app for creating and modifying [Strudel](https://strudel.cc) patterns (live coding music, house/techno) through natural language chat, with audio playback directly in the browser.

## How it works

- **Pattern editor** (left): shows the current Strudel code, with play/stop.
- **Chat** (right): type requests like "more bass", "add an intro", or conceptual questions ("what does `lpf` do?").
- The backend (Flask) uses **OpenAI** with *function calling*: the model decides on its own whether the request implies rewriting the pattern (in which case it calls the `update_pattern` tool and the code updates both in the editor and in the message) or whether it's just a question (text answer, no code).
- Before answering, the backend does **retrieval** over the Strudel documentation (`content/strudel-docs/`) to give the model relevant context on functions/syntax (RAG).
- If configured, the model also has access to a second tool, **`search_web`** (Tavily), for information not covered by the local documentation (news, general music production topics).
- All OpenAI calls (and RAG retrieval) are traced with **LangSmith**, if configured, for debugging/observability (prompts, retrieval, tool calls, latency).
- Audio playback uses `@strudel/web` (`evaluate()`/`hush()`), the same engine as the official REPL, loaded via CDN directly on the page (no bundler needed).

## Setup

Requires [uv](https://docs.astral.sh/uv/) as the package/environment manager.

```bash
uv sync
cp .env.example .env
```

Open `.env` and add your `OPENAI_API_KEY` (required). `TAVILY_API_KEY` and the `LANGSMITH_*` variables are optional: without them the app works the same, just without web search and without tracing. Then:

```bash
uv run app.py
```

Open http://localhost:5000. On the first Play click, `@strudel/web` downloads the drum samples from GitHub (`dirt-samples`): the first playback may take a few seconds.

## RAG: two modes

Retrieval also works **without embeddings**, with a lexical fallback (shared-word counting), so the app is usable right after setup. For real semantic retrieval:

```bash
uv run scripts/build_embeddings.py
```

Generates `data/embeddings.json` (requires `OPENAI_API_KEY` in `.env`, uses `text-embedding-3-small`). The file isn't version-controlled: regenerate it whenever you change the files in `content/strudel-docs/`.

## Expanding the documentation

`content/strudel-docs/*.md` is a **hand-curated** corpus (mini-notation, rhythm, effects, melody, arrangement) meant as a starting point, not a full copy of Strudel's official documentation. To improve the assistant's answers:

1. Add/edit `.md` files in `content/strudel-docs/` (one `##` section per concept/function: it becomes an independent chunk).
2. Re-run `uv run scripts/build_embeddings.py`.

## Web search (Tavily)

If you set `TAVILY_API_KEY` in `.env`, the `search_web` tool is automatically added to the OpenAI call: the model uses it when the local documentation isn't enough (e.g. generic music production questions, recent news). Without the key, the tool isn't even exposed to the model: no different behavior, no error.

## Tracing (LangSmith)

Setting `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env` traces every `/api/chat` request on [smith.langchain.com](https://smith.langchain.com): RAG retrieval, OpenAI calls (including tool calls), and any Tavily searches show up as a single nested pipeline (`strudel_chat_pipeline`), useful for debugging prompts and understanding why the model answered the way it did. `LANGSMITH_PROJECT` sets the project name on LangSmith (default: "default" if omitted). Without these variables the app works normally, without tracing.

## Project structure

```
app.py                     # Flask: "/" route (page) and "/api/chat" (RAG + OpenAI + tool loop)
system_prompt.py           # builds the system prompt (cheatsheet + retrieved docs + current pattern)
web_search.py              # search_web tool (Tavily), traced with @traceable
rag/
  corpus.py                # loads and chunks the .md files in content/strudel-docs
  embeddings.py            # load/save embeddings, cosine similarity
  retrieve.py              # retrieval (semantic if embeddings exist, lexical otherwise)
scripts/build_embeddings.py  # generates data/embeddings.json
content/strudel-docs/*.md  # curated documentation corpus
templates/index.html       # page (editor + chat)
static/css/style.css
static/js/app.js           # editor, chat, audio playback (@strudel/web via CDN)
```

## Stack

Flask (Python) for the backend and to serve the frontend, managed with [uv](https://docs.astral.sh/uv/) (`pyproject.toml` + `uv.lock`); `openai` SDK for chat (function calling) and embeddings, `langsmith` for tracing/observability, `tavily-python` for web search; vanilla HTML/CSS/JS on the client, `@strudel/web` via CDN for audio in the browser — no frontend bundler/build step.

## License note

Strudel is distributed under the **AGPL-3.0** license. By embedding the `@strudel/*` packages in a web app, the AGPL may require that the source code of the entire application be made available under a compatible license — worth verifying (ideally with a lawyer) before commercial production use.
