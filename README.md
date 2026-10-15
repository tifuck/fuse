<img width="1262" height="1390" alt="image" src="https://github.com/user-attachments/assets/1f34a969-2417-4c3e-aaae-032f3e5dbec4" />


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

**Your domain.** Both installers ask for an optional domain (or take `--domain fuse.example.com`). It becomes `PUBLIC_BASE_URL`, the address in invite links and in the connector install line on the Machines page. Machines verify Fuse's certificate, so connectors need that domain to have a trusted one. By default the installer gets a free Let's Encrypt certificate with [Caddy](https://caddyserver.com). This needs the domain's DNS pointing at the server and ports 80 and 443 open. Already running nginx or another proxy? Pass `--no-caddy` and point it at `https://127.0.0.1:8088`. Without a domain, the dashboard works by IP with a self-signed certificate, but machines can't install the connector.

### Option 1: Docker (simplest)

Requires Docker with the Compose plugin (2.24 or newer).

```bash
git clone https://github.com/tifuck/fuse.git && cd fuse
./install.sh --docker            # or: ./install.sh --docker --domain fuse.example.com
```

This writes `.env` with a random `APP_SECRET` and database password, asks for your domain, builds the image (including the connector binaries), and starts Fuse, a bundled MariaDB, and Caddy if you chose a domain (`docker-compose.caddy.yml`, enabled through `COMPOSE_FILE` in `.env`). Open `https://your-domain` (or `https://localhost:8088`) and register the admin account.

- Logs: `docker compose logs -f fuse`
- Update: `git pull && docker compose up -d --build`
- Data lives in the `db`, `data`, and `certs` volumes.

Without the installer: create `.env` with `APP_SECRET=` (48+ random characters) and `DB_PASSWORD=` (hex), then run `docker compose up -d --build`. Optional settings go in `config.env` (see `config.env.example`).

### Option 2: Installer on a Linux host

On Debian or Ubuntu, as root:

```bash
git clone https://github.com/tifuck/fuse.git && cd fuse
./install.sh                     # or: ./install.sh -y --domain fuse.example.com
```

It asks its questions first, then runs eight steps:

1. Writes `.env` with a random `APP_SECRET` and asks for your domain. An existing `.env` is kept.
2. Creates a Python virtual environment in `venv/`.
3. Installs the Python dependencies.
4. Sets up the database: installs `mariadb-server` if missing, creates a `fuse` database and user with a random password, and creates Fuse's tables (`scripts/init_db.py`).
5. Generates a self-signed TLS certificate in `certs/` for direct access on port 8088.
6. Builds the `fuse-agent` connector binaries. This needs `gcc` and `libssl-dev`, plus Go 1.25+ for the Windows connector.
7. Installs and starts a `fuse.service` systemd unit.
8. With a domain, installs Caddy and serves `https://your-domain`. An existing Caddy config is never touched: the installer prints the site block to add instead.

Flags: `-y` (no prompts), `--domain NAME`, `--no-caddy`, `--no-service`, `--database-url URL`, `--help`. Re-running it is safe: existing config, certs and tables are kept. To add a domain later, re-run it with `--domain`.

**Bring your own database:** create an empty MariaDB database and a user with full rights on it, then:

```bash
./install.sh --database-url 'mysql://fuse:PASSWORD@db-host:3306/fuse'
```

Without root, this is the only way to set up the database. The service is then installed as a systemd user unit.

### After installing

- **The first account to register becomes the admin.** After that, registration is invite-only unless `ALLOW_REGISTER=true`.
- Add a model source under **Admin › Sources** (for example a llama.cpp or OpenAI-compatible URL and key), or link a subscription under **Sources**.
- Connect machines from **Machines**: the install line there uses your domain (`PUBLIC_BASE_URL`). If you skipped the domain, re-run the installer with `--domain`.
- Behind your own reverse proxy, have it reach Fuse from `127.0.0.1` or set `FORWARDED_ALLOW_IPS` to its address. Otherwise every user shares the proxy's IP for rate limits. Optionally set `TRUSTED_HOSTS` to your domain.

## Configuration

Settings live in `config.env` and secrets in `.env` (`.env` wins if both set a key; the environment wins over both). `config.env.example` and `.env.example` document them; the full list, with defaults, is in `app/config.py`. The common ones:

| Variable | File | Purpose |
| --- | --- | --- |
| `APP_SECRET` | `.env` | Required. Signs sessions and encrypts stored provider credentials. Changing it invalidates both. |
| `DATABASE_URL` | `.env` | `mysql://user:password@host:3306/database` |
| `PUBLIC_BASE_URL` | `config.env` | Public HTTPS URL (`https://your-domain`), used in connector install lines, invite links and desktop updates. Needs a trusted certificate. |
| `ALLOW_REGISTER` | `config.env` | Open registration (default: invite-only after the first user) |
| `LLAMA_BASE_URL`, `LLAMA_API_KEY` | `config.env`, `.env` | Optional site-wide llama.cpp backend |
| `BRAVE_SEARCH_API_KEY` | `.env` | Optional extra web search source |
| `GITHUB_TOKEN` | `.env` | Optional, raises the GitHub rate limit for skill installs |

Back up the database with `./backup-db.sh`. Keep `.env`, `config.env` and `data/vapid.json` too (or set `VAPID_PRIVATE_KEY`/`VAPID_PUBLIC_KEY` in `.env`): regenerating the VAPID keys breaks existing push subscriptions.

## Updating

Bare metal: `git pull && ./venv/bin/pip install -r requirements.txt && sudo systemctl restart fuse`.
Docker: `git pull && docker compose up -d --build`.

## Development

[`docs/development.md`](docs/development.md) covers the code layout, model backends, conventions, and how to run the tests (pytest against disposable MariaDB schemas, Node unit suites, Playwright browser suites).

## A note on subscription pools

Subscription pools sign in through each provider's own CLI login flow. Using them through a shared router may conflict with a provider's terms of service; that is between you and the provider.

## License

MIT, see `LICENSE`. Vendored code under `agent/third_party/`, `static/vendor/`, and `static/dict/` keeps its own license.
