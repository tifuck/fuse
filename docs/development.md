# Developing Fuse

Fuse is an OpenAI-compatible router (`/v1`) plus a dashboard (chat, coding
agent, admin). Python package: `app/` (entry `app.main:app`). No frontend
build: Jinja templates and plain JS/CSS are served as-is.

## Layout

| Path | What lives there |
| --- | --- |
| `app/` | The server. Large modules keep stable facades (`app/chats.py`, `app/chat_store.py` re-export their owner modules); new code imports the owner. |
| `app/web/` | Dashboard HTTP routes (`auth`, `hosts`, `skills`, `schedules`, `dashboard`, `models`, `chat`, `notifications`, `artifacts`, `sources`, `settings`); `deps.py` holds shared auth/render helpers. No domain SQL here. |
| `app/sources/` | Model backends, one module per type, registered in `app/sources/__init__.py` (`_BUILDERS` + `TYPES`). |
| `app/chat_generation/` | Dashboard generation loop pieces (parsing, streaming, resume policy, tool rounds, run inbox). |
| `app/search/` | Fuse-hosted web search: one file per provider under `providers/`. |
| `app/workspace_ops/` | Workspace tool families used by `app/workspace.py`. |
| `agent/` | `fuse-agent` connector: C binary (`src/`, `make`) and Windows Go EXE (`windows/`). |
| `templates/`, `static/` | Pages and assets. Chat UI: `static/chat.js` + `static/chat/`, rendering in `static/themes/console/`. Vendored code: `static/vendor/`, `static/dict/`. |
| `scripts/` | Operational scripts (database backup, schema dump/init, tool-failure review, usage rollup rebuild). |
| `scripts/sql/` | `schema.sql` (the empty schema fresh installs load) and dated migrations not yet applied everywhere. |
| `tests/` | pytest suites, Node unit suites (`*.test.cjs`) and Playwright browser suites (`*.browser.cjs`). |
| `fuse-desktop/` | Electron wrapper around the site (no app logic). `npm run dist` takes the site URL and app id from `fuse-desktop/site.json` (gitignored) or `FUSE_URL`/`FUSE_APP_ID`; keep the app id stable once released. |

## Configuration

`app/config.py` lists every setting with its default. It reads `config.env`
(settings), then `.env` (secrets, which win), then the process environment.
Templates: `config.env.example` and `.env.example`. Keep secrets out of
`config.env`. `start.sh` owns the uvicorn flags; the systemd unit and the
Docker entrypoint both run it.

## Model sources

Types: `llama`, `openai`, `ollama`, `openrouter`, `explabs` (site or per-user
keys) and the shared subscription pools `grok`, `chatgpt`, `zai`, `devin`,
`kimi`, `mimo`, `claude`, `cursor`, `nanogpt` (`POOL_TYPES`). New upstream models need no code
change: each pool merges its live catalog into its seed list.

- Pools route only for users who may use them (`user_has_*`; always pass the
  `allow_*=` flags and `user_id=` into `pick` / `models_union`).
  `pool_policy.filter_credentials` decides which donated accounts a request
  may use; a healthy session pin always wins over load balancing.
- `openrouter` and `explabs` are per-account keys only. OpenRouter ids are
  `openrouter/{author}/{slug}`; Experiential Labs ids are `explabs/{model}`.
- `nanogpt` ids are `nanogpt/{nanogpt id}`. Its subscription models may use
  shared keys only when the key cannot spend its owner's balance; paid models
  use only the caller's own key (`app/sources/nanogpt.py`).
- Probes never run completions on pooled subscriptions: they check quota
  and/or catalog only.
- Permission auto-approve (`app/jev.py`) asks OpenRouter's decisions API and
  is available only to users with their own OpenRouter key. It fails closed.

## Web conventions

- Starlette needs `templates.TemplateResponse(request, name, context)`.
- Static URLs are content-versioned by `app/static_assets.py`: templates use
  `{{ asset('file.js') }}` and imports are plain relative URLs. Never add
  `?v=` by hand.
- `static/app.css` is an ordered import list; keep its order (cascade).
  Design tokens live in `static/styles/tokens/root.css`.

## Stored transcripts

- Large inline images in `chat_runs.provider_messages`/`request` and
  `chat_threads.retained_history` are kept once per thread in `chat_images`
  (`app/chat_images.py`); the stored JSON carries `fuse-image:<sha256>`.
  Writers go through `chat_images.store`/`store_request`; `api._invoke_chat`
  puts the original bytes back before a request reaches a provider. Code that
  hashes or compares provider rows uses the canonical form
  (`chat_images.canonical_row`/`canonical_content`).
- Those rows can be tens of MB: never `SELECT *` from `chat_runs` or
  `chat_threads` on a hot path; name the columns.

## Tests

Install the test tooling: `pip install pytest` and `npm install playwright`
(then `npx playwright install chromium` for the browser suites).

- **pytest:** `FUSE_CHAT_TEST_MODE=1 python -m pytest tests` (that flag
  enables the MariaDB-backed modules). Each database test gets a disposable
  `fuse_fixture_*` schema through `tests/mariadb_fixture.py`, using a
  dedicated MariaDB account (`FUSE_FIXTURE_DB_USER`, default `fuse_fixture`;
  `FUSE_FIXTURE_DB_PASSWORD`; `FUSE_FIXTURE_DB_SOCKET`). The fixture prints
  the provisioning SQL when the account is missing. `tests/conftest.py`
  refuses connections to real schemas. Tests mock httpx: never hit search
  providers or pooled models.
- **Node unit suites:** `node --test tests/*.test.cjs`.
- **Browser suites:** `scripts/run_browser_tests.sh [tests/x.browser.cjs ...]`. Each
  suite gets its own fixture server (`tests/chat_browser_fixture.py`).
- **Connector:** `make -C agent test`; Windows: `make -C agent/windows test`.

## Schema changes

Write a dated, idempotent migration in `scripts/sql/`, apply it to each
database, then regenerate `scripts/sql/schema.sql` with
`scripts/dump_schema.sh` (`tests/test_init_db_mariadb.py` fails while it is
stale). Delete the dated file once every deployment has it.

## Connector releases

Bump `agent/VERSION`, then `make -C agent dist` builds every SKU into
`agent/dist`, which Fuse serves: Linux amd64, Linux arm64 (needs
`gcc-aarch64-linux-gnu` and a static aarch64 OpenSSL via `OPENSSL_ARM64`) and
Windows (needs Go 1.25+). Agents update when the served version is newer than
theirs.
