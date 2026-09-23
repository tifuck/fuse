#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -d venv ]; then
  . venv/bin/activate
fi
# APP_SECRET must come from the environment or .env. Never default it here —
# pydantic-settings prefers process env over .env, so a fallback would hide a
# strong file secret.
SSL_CERT="${SSL_CERTFILE:-certs/server.crt}"
SSL_KEY="${SSL_KEYFILE:-certs/server.key}"
if [ ! -f "$SSL_CERT" ] || [ ! -f "$SSL_KEY" ]; then
  echo "TLS cert/key missing: $SSL_CERT $SSL_KEY" >&2
  exit 1
fi
exec python -m uvicorn app.main:app \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-8088}" \
  --limit-concurrency "${LIMIT_CONCURRENCY:-64}" \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
  --ssl-certfile "$SSL_CERT" \
  --ssl-keyfile "$SSL_KEY"
