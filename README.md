# Fuse

Fuse is a self-hosted LLM router and workspace. It gives every user an OpenAI-compatible API (`/v1/chat/completions`, `/v1/models`) that routes to whichever model backends the site has configured, plus a web dashboard with a chat client that can use tools, search the web, and run as a coding agent on your own machines.

One Fuse instance serves many users. The first account registered becomes the admin, who configures site-wide model sources and invites everyone else. Users can add their own keys and subscription logins on top.

## Features

**Routing and API**
- OpenAI-compatible `/v1` endpoint with per-user API keys, usage tracking, and per-model pricing.
- Model sources: a llama.cpp server, any OpenAI-compatible endpoint (with key rotation), Ollama Cloud, and per-user OpenRouter keys.
- Shared subscription pools: users can link Grok, ChatGPT, Claude, Z.AI (GLM Coding Plan), Kimi Code, Devin and Xiaomi MiMo accounts. They choose whether each account is shared with everyone, with other contributors, or kept private. New upstream models appear automatically.

**Dashboard chat**
- Streaming chat with tool calls, subagents, background commands and monitors, and live sync across tabs and devices.
- Built-in web search and page reading, with no search API key needed (an optional Brave key adds a source).
- Context meter with automatic compaction for long conversations.
- Artifacts (shareable HTML pages), skills (install from GitHub or skills.sh, or write your own), scheduled runs, and Web Push notifications when a reply finishes.

**Coding agent on your machines**
- Connect machines with the `fuse-agent` connector (Linux binary or Windows EXE; outbound HTTPS only) or over SSH.
- Projects per machine, permission prompts for commands and files, and per-user encrypted host secrets.

**Admin**
- Users, invites, model sources, pools, pricing, retention (auto-archive/delete), and a bug-report inbox.
- Optional desktop app (`fuse-desktop/`, Electron wrapper around the site).

## Setup

Fuse needs **MariaDB 10.11+** (MySQL is not supported) and **Python 3.11+** (Debian 12, Ubuntu 24.04 or newer), and serves HTTPS itself. Both installers below create the database tables for you.

### Option 1: Docker (simplest)

Requires Docker with the Compose plugin.

```bash
git clone https://github.com/tifuck/fuse.git && cd fuse
./install.sh --docker
```

This writes `.env` with a random `APP_SECRET` and database password, builds the image (including the connector binaries), and starts Fuse and a bundled MariaDB. Open `https://localhost:8088` and register the admin account.

- Logs: `docker compose logs -f fuse`
- Update: `git pull && docker compose up -d --build`
- Data lives in the `db`, `data`, and `certs` volumes.

Without the installer: create `.env` with `APP_SECRET=` (48+ random characters) and `DB_PASSWORD=` (hex), then run `docker compose up -d --build`.

### Option 2: Installer on a Linux host

On Debian or Ubuntu, as root:

```bash
git clone https://github.com/tifuck/fuse.git && cd fuse
./install.sh
```

The installer runs seven steps:

1. Creates a Python virtual environment in `venv/`.
2. Installs the Python dependencies.
3. Writes `.env` with a random `APP_SECRET`. An existing `.env` is kept.
4. Sets up the database: installs `mariadb-server` if missing, creates a `fuse` database and user with a random password, and creates Fuse's tables (`scripts/init_db.py`).
5. Generates a self-signed TLS certificate in `certs/`.
6. Builds the `fuse-agent` connector binaries. This needs `gcc` and `libssl-dev`, plus Go 1.25+ for the Windows connector.
7. Installs and starts a `fuse.service` systemd unit.

Flags: `-y` (no prompts), `--no-service`, `--database-url URL`, `--help`. Re-running it is safe: existing config, certs and tables are kept.

**Bring your own database:** create an empty MariaDB database and a user with full rights on it, then:

```bash
./install.sh --database-url 'mysql://fuse:PASSWORD@db-host:3306/fuse'
```

Without root, this is the only way to set up the database. The service is then installed as a systemd user unit.

### After installing

- **The first account to register becomes the admin.** After that, registration is invite-only unless `ALLOW_REGISTER=true`.
- Add a model source under **Admin › Sources** (for example a llama.cpp or OpenAI-compatible URL and key), or link a subscription under **Sources**.
- To connect machines with `fuse-agent`, set `PUBLIC_BASE_URL` in `.env` to the HTTPS address the machines can reach.
- For a public site, put Fuse behind a reverse proxy with a real certificate, and set `TRUSTED_HOSTS` and `FORWARDED_ALLOW_IPS`.

## Configuration

Settings come from `.env` (or the environment). The full list, with defaults, is in `app/config.py`. The common ones:

| Variable | Purpose |
| --- | --- |
| `APP_SECRET` | Required. Signs sessions and encrypts stored provider credentials. Changing it invalidates both. |
| `DATABASE_URL` | `mysql://user:password@host:3306/database` |
| `PUBLIC_BASE_URL` | Public HTTPS URL, used for connector installs and desktop updates |
| `ALLOW_REGISTER` | Open registration (default: invite-only after the first user) |
| `LLAMA_BASE_URL`, `LLAMA_API_KEY` | Optional site-wide llama.cpp backend |
| `BRAVE_SEARCH_API_KEY` | Optional extra web search source |
| `GITHUB_TOKEN` | Optional, raises the GitHub rate limit for skill installs |

Back up the database with `./backup-db.sh`. Keep `.env` and `data/vapid.json` too: regenerating the VAPID keys breaks existing push subscriptions.

## Updating

Bare metal: `git pull && ./venv/bin/pip install -r requirements.txt && sudo systemctl restart fuse`.
Docker: `git pull && docker compose up -d --build`.

## Development

`AGENTS.md` documents the code layout, model backends, and conventions. Tests run with pytest against disposable MariaDB schemas (see the Layout section of `AGENTS.md`).

## A note on subscription pools

Subscription pools sign in through each provider's own CLI login flow. Using them through a shared router may conflict with a provider's terms of service; that is between you and the provider.

## License

MIT, see `LICENSE`. Vendored code under `third_party/`, `agent/third_party/`, and `static/dict/` keeps its own license.
