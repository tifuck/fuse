#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
BACKUP_DIR="${FUSE_BACKUP_DIR:-${1:-backups}}"
exec "$ROOT/venv/bin/python" -m scripts.backup_mariadb "$BACKUP_DIR"
