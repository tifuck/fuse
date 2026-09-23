#!/usr/bin/env bash
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BACKUP_DIR="${FUSE_BACKUP_DIR:-${1:-backups}}"
DB_PATH="${FUSE_DB_PATH:-data/fuse.db}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
if ./venv/bin/python -c 'from app.config import settings; raise SystemExit(not settings.database_mariadb_enabled)'; then
  exec ./venv/bin/python -m scripts.backup_mariadb "$BACKUP_DIR"
fi
OUT="$BACKUP_DIR/fuse-db-$STAMP.db.gz"
RETAIN="${FUSE_BACKUP_RETAIN_DB:-14}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ -f "$DB_PATH" ] || die "database not found: $DB_PATH (run backup-db.sh from the Fuse repo root)"

PY="python3"
[ -x venv/bin/python ] && PY="venv/bin/python"

mkdir -p "$BACKUP_DIR"
TMP_DB="$BACKUP_DIR/.fuse-db-$STAMP.tmp"
rm -f "$TMP_DB"

"$PY" - "$DB_PATH" "$TMP_DB" <<'PYEOF'
import sqlite3
import sys

src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src)
d = sqlite3.connect(dst)
with d:
    s.backup(d)
s.close()
check = d.execute("PRAGMA integrity_check").fetchone()[0]
d.close()
if check != "ok":
    sys.exit(f"integrity_check failed: {check}")
PYEOF

gzip -9 -c "$TMP_DB" > "$OUT"
rm -f "$TMP_DB"
sha256sum "$OUT" > "$OUT.sha256"

mapfile -t olds < <(ls -1 "$BACKUP_DIR"/fuse-db-*.db.gz 2>/dev/null | sort -r)
if [ "${#olds[@]}" -gt "$RETAIN" ]; then
  for old in "${olds[@]:RETAIN}"; do
    rm -f "$old" "$old.sha256"
  done
  printf 'Pruned old database backups (kept last %s).\n' "$RETAIN"
fi

SIZE="$(du -h --apparent-size "$OUT" | cut -f1)"

cat <<EOF

Database backup complete.

  Archive:  $OUT ($SIZE)
  Checksum: $OUT.sha256
  Source:   $DB_PATH (live snapshot via sqlite backup API, integrity_check passed)

Restore (stop the fuse service first):

  zcat $(basename "$OUT") > data/fuse.db
EOF
