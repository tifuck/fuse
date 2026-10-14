# Fuse

OpenAI-compatible LLM router (`/v1/chat/completions`, `/v1/models`) with a web dashboard: chat with tool use, coding agents on connected machines, and pooled model backends (llama.cpp, OpenAI-compatible, Ollama Cloud, OpenRouter, and subscription pools).

## Run

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
# write .env: APP_SECRET, DATABASE_URL, LLAMA_BASE_URL, LLAMA_API_KEY
./start.sh
```

The first registered user is admin. Configuration is read from `.env` by pydantic-settings (`app/config.py`).

`AGENTS.md` documents the layout, backends, and conventions in detail.

## License

MIT, see `LICENSE`. Vendored code under `third_party/`, `agent/third_party/`, and `static/dict/` keeps its own license.
