# Developing Fuse

Fuse is an OpenAI-compatible router (`/v1`) plus a dashboard (chat, coding
agent, admin). Python package: `app/` (entry `app.main:app`). No frontend
build: Jinja templates and plain JS/CSS are served as-is.

## Layout

| Path | What lives there |
| --- | --- |
| `app/` | The server. Large modules keep stable facades (`app/chats.py`, `app/chat_store.py` re-export their owner modules); new code imports the owner. |
| `app/web/` | Dashboard HTTP routes (`auth`, `hosts`, `issues`, `skills`, `schedules`, `dashboard`, `models`, `chat`, `notifications`, `artifacts`, `sources`, `settings`); `deps.py` holds shared auth/render helpers. No domain SQL here. |
| `app/sources/` | Model backends, one module per type, registered in `app/sources/__init__.py` (`_BUILDERS` + `TYPES`). |
| `app/chat_generation/` | Dashboard generation loop pieces (parsing, streaming, resume policy, tool rounds, run inbox). |
| `app/search/` | Fuse-hosted web search: one file per provider under `providers/`. |
| `app/workspace_ops/` | Workspace tool families used by `app/workspace.py`. |
| `agent/` | `fuse-agent` connector: C binary (`src/`, `make`) and Windows Go EXE (`windows/`). |
| `templates/`, `static/` | Pages and assets. Chat UI: `static/chat.js` + `static/chat/`, rendering in `static/themes/console/`. Vendored code: `static/vendor/`, `static/dict/`. |
| `scripts/` | Operational scripts (backups, schema dump/init, usage rollup rebuild, connector release). `scripts/qa/` holds manual QA tools that are not part of the test suite. |
| `scripts/sql/` | `schema.sql` (the empty schema fresh installs load) and dated migrations not yet applied everywhere. |
| `tests/` | pytest suites, Node unit suites (`*.test.cjs`) and Playwright browser suites (`*.browser.cjs`). |
| `fuse-desktop/` | Electron wrapper around the site (no app logic). `npm run dist` takes the site URL and app id from `fuse-desktop/site.json` (gitignored) or `FUSE_URL`/`FUSE_APP_ID`; keep the app id stable once released. |
| `third_party/` | SCOWL word lists, the source of `static/dict/en-US.bin.gz` (`scripts/build_composer_dict.py`). |

## Configuration

`app/config.py` lists every setting with its default. It reads `config.env`
(settings), then `.env` (secrets, which win), then the process environment.
Templates: `config.env.example` and `.env.example`. Keep secrets out of
`config.env`. `start.sh` owns the uvicorn flags; the systemd unit and the
Docker entrypoint both run it.

## Model sources

Types: `llama`, `openai`, `ollama`, `openrouter`, `explabs` (site or per-user
keys) and the shared subscription pools `grok`, `chatgpt`, `zai`, `devin`,
`kimi`, `mimo`, `claude` (`POOL_TYPES`). New upstream models need no code
change: each pool merges its live catalog into its seed list.

- Pools route only for users who may use them (`user_has_*`; always pass the
  `allow_*=` flags and `user_id=` into `pick` / `models_union`).
  `pool_policy.filter_credentials` decides which donated accounts a request
  may use; a healthy session pin always wins over load balancing.
- `openrouter` and `explabs` are per-account keys only. OpenRouter ids are
  `openrouter/{author}/{slug}`; Experiential Labs ids are `explabs/{model}`.
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

## Tests

Install the tooling: `pip install -r requirements-dev.txt` and `npm install`
(Playwright for the browser suites, then `npx playwright install chromium`).

- **pytest:** `FUSE_CHAT_TEST_MODE=1 python -m pytest tests` (that flag
  enables the MariaDB-backed modules). Each database test gets a disposable
  `fuse_fixture_*` schema through `tests/mariadb_fixture.py`, using a
  dedicated MariaDB account (`FUSE_FIXTURE_DB_USER`, default `fuse_fixture`;
  `FUSE_FIXTURE_DB_PASSWORD`; `FUSE_FIXTURE_DB_SOCKET`). The fixture prints
  the provisioning SQL when the account is missing. `tests/conftest.py`
  refuses connections to real schemas. Tests mock httpx: never hit search
  providers or pooled models.
- **Node unit suites:** `npm run test:unit` (`node --test tests/*.test.cjs`).
- **Browser suites:** `npm run test:browser [tests/x.browser.cjs ...]`. Each
  suite gets its own fixture server (`tests/chat_browser_fixture.py`).
- **Connector:** `make -C agent test`; Windows: `make -C agent/windows test`.

## Schema changes

Write a dated, idempotent migration in `scripts/sql/`, apply it to each
database, then regenerate `scripts/sql/schema.sql` with
`scripts/dump_schema.sh` (`tests/test_init_db_mariadb.py` fails while it is
stale). Delete the dated file once every deployment has it.

## Connector releases

Bump `agent/VERSION`, then `scripts/release-connector.sh` builds every SKU
(needs Go 1.25+ and a static aarch64 OpenSSL at `$OPENSSL_ARM64`) and stages
the binaries into the serving tree's `agent/dist`. Agents update when the
served version is newer than theirs.
