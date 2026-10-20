#!/usr/bin/env bash
# The one place the server's uvicorn flags live: the systemd unit written by
# install.sh and the Docker entrypoint both run this script.
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
# glibc keeps freed memory in per-thread arenas and raises its mmap threshold
# after each large free, so multi-MB transcripts parsed on the worker threads
# stay resident for the life of the process. Two arenas and a fixed 1 MiB
# threshold hand large blocks back to the OS when they are freed.
export GLIBC_TUNABLES="${GLIBC_TUNABLES:-glibc.malloc.arena_max=2:glibc.malloc.mmap_threshold=1048576}"
exec python -m uvicorn app.main:app \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-8088}" \
  --workers 1 \
  --limit-concurrency "${LIMIT_CONCURRENCY:-256}" \
  --timeout-graceful-shutdown 8 \
  --ws-max-size 1048576 \
  --ws-max-queue 32 \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
  --ssl-certfile "$SSL_CERT" \
  --ssl-keyfile "$SSL_KEY"
