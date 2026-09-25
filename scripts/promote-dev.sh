#!/usr/bin/env bash
# Fast-forward production to the dev branch. Code only: .env, certs, data,
# venv, and backups stay put. Schema changes are not applied here; they ship
# as their own reviewed step against fuse_live.
set -euo pipefail

PROD="${FUSE_PROD_ROOT:-/root/fuse}"
DEV="${FUSE_DEV_ROOT:-/root/fuse-dev}"
PROD_BRANCH="${FUSE_PROD_BRANCH:-main}"
DEV_BRANCH="${FUSE_DEV_BRANCH:-dev}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root"
git -C "$PROD" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "production is not a git repo: $PROD"
git -C "$DEV" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "development worktree missing: $DEV"
[ "$PROD" != "$DEV" ] || die "dev and production are the same directory"

cd "$PROD"
[ "$(git rev-parse --abbrev-ref HEAD)" = "$PROD_BRANCH" ] || die "production is not on $PROD_BRANCH"
git diff --quiet && git diff --cached --quiet || die "production worktree is dirty; commit or stash first"
[ -z "$(git status --porcelain --untracked-files=normal)" ] || die "production has untracked files; review them first"

cd "$DEV"
[ "$(git rev-parse --abbrev-ref HEAD)" = "$DEV_BRANCH" ] || die "development is not on $DEV_BRANCH"
git diff --quiet && git diff --cached --quiet || die "development worktree is dirty; commit first"
[ -z "$(git status --porcelain --untracked-files=normal)" ] || die "development has untracked files; commit or remove them first"
git rev-parse --verify "$DEV_BRANCH" >/dev/null || die "missing branch $DEV_BRANCH"

# HTML is no-store, but /static is cached by URL. Raise ?v= on every template
# reference whose file changed since production, then commit that on dev so the
# fast-forward below carries it. A no-op when nothing referenced changed.
python3 - "$PROD" "$DEV" <<'PY'
import pathlib, re, subprocess, sys
prod, dev = sys.argv[1:]
base = subprocess.check_output(["git", "-C", prod, "rev-parse", "HEAD"], text=True).strip()
changed = subprocess.check_output(
    ["git", "-C", dev, "diff", "--name-only", base, "HEAD"], text=True)
changed = {line.strip() for line in changed.splitlines() if line.strip()}
pat = re.compile(r'((?:src|href)="/static/([^"?]+)\?v=)(\d+)')
bumped = []
for path in pathlib.Path(dev, "templates").rglob("*.html"):
    text = path.read_text()
    def repl(m):
        if "static/" + m.group(2) not in changed:
            return m.group(0)
        bumped.append(f"{m.group(2)} {m.group(3)}->{int(m.group(3)) + 1}")
        return f"{m.group(1)}{int(m.group(3)) + 1}"
    new = pat.sub(repl, text)
    if new != text:
        path.write_text(new)
if bumped:
    print("cache bust:", ", ".join(sorted(set(bumped))))
PY
if ! git -C "$DEV" diff --quiet; then
  git -C "$DEV" add templates
  git -C "$DEV" commit -m "Bump static cache versions for files changed since production"
fi

cd "$PROD"
git fetch "$DEV" "$DEV_BRANCH"
git merge-base --is-ancestor "$PROD_BRANCH" FETCH_HEAD || die "$DEV_BRANCH is not a fast-forward of $PROD_BRANCH; rebase dev onto main first"
[ "$(git rev-parse HEAD)" = "$(git rev-parse FETCH_HEAD)" ] && die "nothing to promote"

BEFORE="$(git rev-parse --short HEAD)"
"$PROD/backup-db.sh"
git merge --ff-only FETCH_HEAD
AFTER="$(git rev-parse --short HEAD)"

# requirements.txt changes are installed by hand in the dev venv, then
# mirrored into the prod venv before this script runs. fuse.service has
# NoNewPrivileges, so pip in that venv cannot re-exec from here.
if ! cmp -s "$DEV/requirements.txt" "$PROD/requirements.txt"; then
  die "requirements.txt changed; install it into $PROD/venv before promoting"
fi

systemctl restart fuse
sleep 2
systemctl is-active --quiet fuse || die "fuse.service did not come back; roll back with: git -C $PROD reset --hard $BEFORE && systemctl restart fuse"

printf 'Promoted %s -> %s (%s..%s). fuse.service is active.\n' "$DEV_BRANCH" "$PROD_BRANCH" "$BEFORE" "$AFTER"
