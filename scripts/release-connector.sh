#!/usr/bin/env bash
# Build every connector SKU at agent/VERSION, check that each binary carries
# that version, and stage them into production's agent/dist. Run it BEFORE
# promoting the VERSION bump: agents update only when the manifest version
# (production's agent/VERSION) is newer than theirs, so staged binaries sit
# unused until the bump lands. A SKU left unbuilt would be served under the
# new number with an older binary, so any missing or stale SKU refuses the
# whole release.
set -euo pipefail

DEV="${FUSE_DEV_ROOT:-/root/fuse-dev}"
PROD="${FUSE_PROD_ROOT:-/root/fuse}"
OPENSSL_ARM64="${OPENSSL_ARM64:-/tmp/opencode/ssl-arm64}"
SKUS="fuse-agent-linux-amd64 fuse-agent-linux-arm64 fuse-agent-windows-amd64.exe fuse-agent-windows-arm64.exe"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Connector shells start without HOME, which go needs.
export HOME="${HOME:-/root}"
export GOPATH="${GOPATH:-$HOME/go}" PATH="/usr/local/go/bin:$PATH"
mkdir -p /tmp/fuse
cd "$DEV/agent"
VER="$(cat VERSION)"
LIVE="$(cat "$PROD/agent/VERSION")"
[ "$VER" != "$LIVE" ] && [ "$(printf '%s\n%s\n' "$LIVE" "$VER" | sort -V | tail -n 1)" = "$VER" ] \
  || die "agent/VERSION $VER is not newer than production's $LIVE; bump it first"
[ -f "$OPENSSL_ARM64/lib/libssl.a" ] \
  || die "no static aarch64 OpenSSL at $OPENSSL_ARM64 (build it: Configure linux-aarch64 no-shared, install_dev)"
command -v go >/dev/null || die "go not found (expected /usr/local/go/bin/go)"

make test >/tmp/fuse/release-connector-test.log 2>&1 || die "make test failed; see /tmp/fuse/release-connector-test.log"
make -B all linux-arm64 windows OPENSSL_ARM64="$OPENSSL_ARM64" >/tmp/fuse/release-connector-build.log 2>&1 \
  || die "build failed; see /tmp/fuse/release-connector-build.log"
for n in $SKUS; do
  [ -s "dist/$n" ] || die "dist/$n was not built"
  grep -qaF "$VER" "dist/$n" || die "dist/$n does not carry version $VER"
done

BACKUP="/tmp/fuse/agent-dist-$LIVE"
mkdir -p "$BACKUP"
cp -a "$PROD/agent/dist/." "$BACKUP/"
for n in $SKUS; do
  install -m 755 "dist/$n" "$PROD/agent/dist/.$n.tmp"
  mv -f "$PROD/agent/dist/.$n.tmp" "$PROD/agent/dist/$n"
  cmp -s "dist/$n" "$PROD/agent/dist/$n" || die "staging $n failed"
done
(cd "$PROD/agent/dist" && sha256sum $SKUS)

cat <<EOF
Staged connector $VER; production still advertises $LIVE (backup: $BACKUP).
Next:
  1. Canary: install $DEV/agent/dist/fuse-agent-linux-amd64 as one host's connector,
     restart it, and check command output, read_file and grep on that host.
  2. Commit the agent/VERSION bump on dev and run scripts/promote-dev.sh.
Rollback before step 2: copy $BACKUP back into $PROD/agent/dist.
EOF
