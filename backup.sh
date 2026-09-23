#!/usr/bin/env bash
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

OUT_DIR="${FUSE_BACKUP_DIR:-${1:-backups}}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$OUT_DIR/fuse-source-$STAMP.tar.gz"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

ITEMS=(
  app
  templates
  static
  agent
  third_party
  tests
  docs
  scripts
  requirements.txt
  start.sh
  .gitignore
  AGENTS.md
  CODING_AGENT.md
  CHAT_REDESIGN_PLAN.md
  CHAT_SYNC.md
  install.sh
  backup.sh
  fuse-desktop/.github
  fuse-desktop/build
  fuse-desktop/scripts
  fuse-desktop/main.js
  fuse-desktop/package.json
  fuse-desktop/package-lock.json
  fuse-desktop/electron-builder.yml
  fuse-desktop/.gitignore
)

OPTIONAL=(
  CHAT_REDESIGN_PLAN.md
  CHAT_SYNC.md
  docs
  scripts
  tests
  third_party
  fuse-desktop/.github
)

is_optional() {
  local item="$1"
  for opt in "${OPTIONAL[@]}"; do
    [ "$item" = "$opt" ] && return 0
  done
  return 1
}

INCLUDE=()
for item in "${ITEMS[@]}"; do
  if [ -e "$item" ]; then
    INCLUDE+=("$item")
  elif is_optional "$item"; then
    warn "skipping missing optional path: $item"
  else
    die "missing required path: $item (run backup.sh from the Fuse repo root)"
  fi
done

mkdir -p "$OUT_DIR"
TMP="$OUT.tmp"
rm -f "$TMP" "$TMP.sha256"

tar -czf "$TMP" \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='agent/tests/test_expand_home' \
  --exclude='agent/tests/test_reconnect' \
  --exclude='agent/tests/test_files' \
  --transform="s,^,fuse/," \
  "${INCLUDE[@]}"

LEAKS="$(tar -tzf "$TMP" | grep -E \
  '(^|/)\.env$|(^|/)\.env\.|(^|/)certs/|\.key$|\.pem$|(^|/)data/|(^|/)venv/|node_modules|(^|/)backups/|\.db($|-)' \
  || true)"
if [ -n "$LEAKS" ]; then
  rm -f "$TMP"
  die "audit failed: forbidden paths found in archive:
$LEAKS"
fi

LISTING="$OUT_DIR/.listing.tmp"
tar -tzf "$TMP" > "$LISTING"

for need in fuse/app/main.py fuse/requirements.txt fuse/install.sh fuse/templates/base.html fuse/static/app.css fuse/start.sh; do
  grep -x "$need" "$LISTING" >/dev/null || { rm -f "$TMP" "$LISTING"; die "integrity check failed: $need missing from archive"; }
done

if ! grep -q '^fuse/agent/dist/' "$LISTING"; then
  warn "no prebuilt agent binaries in archive; they can be rebuilt with: make -C agent dist"
fi
rm -f "$LISTING"

mv "$TMP" "$OUT"
sha256sum "$OUT" > "$OUT.sha256"

RETAIN="${FUSE_BACKUP_RETAIN:-10}"
mapfile -t olds < <(ls -1 "$OUT_DIR"/fuse-source-*.tar.gz 2>/dev/null | sort -r)
if [ "${#olds[@]}" -gt "$RETAIN" ]; then
  for old in "${olds[@]:RETAIN}"; do
    rm -f "$old" "$old.sha256"
  done
  PRUNED=1
fi

COUNT="$(tar -tzf "$OUT" | wc -l)"
SIZE="$(du -h --apparent-size "$OUT" | cut -f1)"

cat <<EOF

Backup complete.

  Archive:  $OUT ($SIZE, $COUNT entries)
  Checksum: $OUT.sha256
  Retention: kept the last $RETAIN code archives (FUSE_BACKUP_RETAIN overrides)

Restore on a new machine:

  tar -xzf $(basename "$OUT")
  cd fuse
  ./install.sh
EOF
