#!/usr/bin/env bash
set -euo pipefail

FUSE_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$FUSE_HOME"

ASSUME_YES=0
WANT_SERVICE=1
PRINT_UNIT=""
FORCE_UNIT=0
SERVICE_INSTALLED=0
SERVICE_MODE=""

say()  { printf '\n=== %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
Usage: ./install.sh [options]

Sets up Fuse end to end in this directory:
  - Python virtual environment (venv/) + pip dependencies
  - .env with a freshly generated APP_SECRET (existing file is never touched)
  - Self-signed TLS certificate in certs/ (existing certs are kept)
  - Optional systemd service (asks; root gets a system unit,
    non-root gets a user unit with lingering enabled)

Options:
  -y, --yes          Non-interactive: accept all defaults (installs the service)
  --no-service       Skip the systemd service entirely
  --force-unit       Overwrite an existing fuse.service that points at a
                     different directory (e.g. reclaiming a stale unit)
  --print-unit MODE  Print the generated unit file and exit (MODE: system|user)
  -h, --help         Show this help
USAGE
}

ask() {
  [ "$ASSUME_YES" -eq 1 ] && return 0
  [ ! -t 0 ] && return 0
  local reply
  read -r -p "$1" reply || true
  case "${reply:-y}" in
    [nN]|[nN][oO]) return 1 ;;
    *) return 0 ;;
  esac
}

gen_unit() {
  local mode="$1" wantedby sandbox
  if [ "$mode" = "user" ]; then
    wantedby="default.target"
    sandbox="NoNewPrivileges=true"
  else
    wantedby="multi-user.target"
    sandbox="NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=false
ReadWritePaths=$FUSE_HOME/data
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=true"
  fi
  cat <<UNIT
[Unit]
Description=Fuse OpenAI-compatible router and dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$FUSE_HOME
EnvironmentFile=$FUSE_HOME/.env
ExecStart=$FUSE_HOME/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port \${PORT:-8088} --workers 1 --limit-concurrency 256 --timeout-graceful-shutdown 8 --ws-max-size 1048576 --ws-max-queue 32 --proxy-headers --forwarded-allow-ips \${FORWARDED_ALLOW_IPS:-127.0.0.1} --ssl-certfile $FUSE_HOME/certs/server.crt --ssl-keyfile $FUSE_HOME/certs/server.key
Restart=on-failure
RestartSec=3
TimeoutStopSec=20
StandardOutput=journal
StandardError=journal
SyslogIdentifier=fuse
$sandbox

[Install]
WantedBy=$wantedby
UNIT
}

guard_existing_unit() {
  # Running this installer from one tree must not silently capture another
  # deployment's service (e.g. a dev worktree rewriting production's unit).
  local unit="$1" root
  [ -f "$unit" ] || return 0
  root="$(grep -E '^WorkingDirectory=' "$unit" | tail -1 | cut -d= -f2-)"
  if [ -n "$root" ] && [ "$root" != "$FUSE_HOME" ]; then
    [ "$FORCE_UNIT" -eq 1 ] || die "$unit already points at $root; pass --force-unit to move it to $FUSE_HOME"
    warn "overwriting $unit (it pointed at $root)"
  fi
}

apt_install() {
  command -v apt-get >/dev/null 2>&1 || return 1
  [ "$(id -u)" -eq 0 ] || return 1
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"
}

install_system_service() {
  local unit=/etc/systemd/system/fuse.service
  guard_existing_unit "$unit"
  gen_unit system > "$unit"
  systemctl daemon-reload
  systemctl enable fuse >/dev/null 2>&1
  systemctl restart fuse
  SERVICE_INSTALLED=1
  SERVICE_MODE=system
}

install_user_service() {
  if ! systemctl --user show-environment >/dev/null 2>&1; then
    warn "systemd user session is not available (no user bus)"
    warn "log in again, or run: export XDG_RUNTIME_DIR=/run/user/$(id -u)"
    warn "skipping service install; you can start Fuse with ./start.sh"
    return 0
  fi
  mkdir -p "$HOME/.config/systemd/user"
  guard_existing_unit "$HOME/.config/systemd/user/fuse.service"
  gen_unit user > "$HOME/.config/systemd/user/fuse.service"
  systemctl --user daemon-reload
  systemctl --user enable fuse >/dev/null 2>&1
  systemctl --user restart fuse
  loginctl enable-linger "$(id -un)" 2>/dev/null \
    || warn "could not enable lingering; for boot-time start run: sudo loginctl enable-linger $(id -un)"
  SERVICE_INSTALLED=1
  SERVICE_MODE=user
}

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1 ;;
    --no-service) WANT_SERVICE=0 ;;
    --force-unit) FORCE_UNIT=1 ;;
    --print-unit)
      shift
      PRINT_UNIT="${1:-}"
      case "$PRINT_UNIT" in system|user) ;; *) die "--print-unit requires system|user" ;; esac
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done

if [ -n "$PRINT_UNIT" ]; then
  gen_unit "$PRINT_UNIT"
  exit 0
fi

printf 'Fuse installer\n'
printf '  directory: %s\n' "$FUSE_HOME"
printf '  user:      %s (uid %s)\n' "$(id -un)" "$(id -u)"

say "Step 1/5: Python virtual environment"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || die "python3 >= 3.9 required (found $(python3 -V 2>&1))"
if [ ! -x venv/bin/python ]; then
  if ! python3 -m venv venv; then
    warn "python3 -m venv failed (python3-venv package missing?)"
    if apt_install python3-venv; then
      python3 -m venv venv
    else
      die "install python3-venv (e.g. sudo apt-get install python3-venv) and re-run"
    fi
  fi
  echo "created venv/"
else
  echo "venv/ already exists, reusing"
fi

say "Step 2/5: Python dependencies"
venv/bin/pip install --disable-pip-version-check --upgrade pip
venv/bin/pip install --disable-pip-version-check -r requirements.txt

say "Step 3/5: Configuration"
if [ ! -f .env ]; then
  APP_SECRET_VALUE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  old_umask="$(umask)"
  umask 077
  cat > .env <<EOF
APP_SECRET=$APP_SECRET_VALUE
HOST=0.0.0.0
PORT=8088

# Public HTTPS URL of this box (required for fuse-agent connector installs
# and desktop update checks):
#PUBLIC_BASE_URL=https://your-host.example.com
# Required: Fuse runs only on a migrated MariaDB schema (scripts/migrate_mariadb.py).
#DATABASE_MARIADB_ENABLED=true
#DATABASE_URL=mysql://fuse:CHANGE_ME@127.0.0.1:3306/fuse_live
#TRUSTED_HOSTS=
#FORWARDED_ALLOW_IPS=127.0.0.1
#OPENAI_AUTH_PROXY=
EOF
  umask "$old_umask"
  echo "generated .env with a fresh random APP_SECRET (chmod 600)"
else
  echo "existing .env kept untouched"
fi

say "Step 4/5: TLS certificate"
if [ -f certs/server.crt ] && [ -f certs/server.key ]; then
  echo "existing certs kept"
else
  if ! command -v openssl >/dev/null 2>&1; then
    apt_install openssl || die "openssl is required to create the TLS certificate"
  fi
  mkdir -p certs
  old_umask="$(umask)"
  umask 077
  openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
    -keyout certs/server.key -out certs/server.crt \
    -subj "/CN=fuse" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" >/dev/null 2>&1
  umask "$old_umask"
  echo "generated self-signed certificate (certs/server.crt)"
fi
mkdir -p data/desktop-releases

say "Step 5/5: Service"
if [ "$WANT_SERVICE" -eq 1 ] && ! { grep -qE '^DATABASE_MARIADB_ENABLED=true' .env && grep -qE '^DATABASE_URL=mysql://' .env; }; then
  # app/db.py refuses to boot without MariaDB: a service now would only
  # crash-loop. Configure it, migrate the schema, then re-run this script.
  warn "MariaDB is not configured in .env (DATABASE_MARIADB_ENABLED=true and DATABASE_URL=mysql://...);"
  warn "skipping service setup. Set both, migrate the schema with scripts/migrate_mariadb.py, then re-run."
  WANT_SERVICE=0
fi
if [ "$WANT_SERVICE" -eq 1 ]; then
  if ask "Install Fuse as a systemd service for user '$(id -un)'? [Y/n] "; then
    if [ "$(id -u)" -eq 0 ]; then
      install_system_service
    else
      install_user_service
    fi
  else
    echo "skipped service setup"
  fi
fi

PORT_VALUE="$(grep -E '^PORT=' .env | tail -1 | cut -d= -f2- || true)"
PORT_VALUE="${PORT_VALUE:-8088}"
ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$ADDR" ] || ADDR="$(hostname 2>/dev/null || echo localhost)"

if [ "$SERVICE_INSTALLED" -eq 1 ]; then
  sleep 2
  if [ "$SERVICE_MODE" = "system" ]; then
    if systemctl is-active --quiet fuse; then
      echo "fuse.service is active"
    else
      warn "service did not reach active state; recent logs:"
      journalctl -u fuse -n 30 --no-pager || true
    fi
  else
    if systemctl --user is-active --quiet fuse; then
      echo "user fuse.service is active"
    else
      warn "service did not reach active state; recent logs:"
      journalctl --user -u fuse -n 30 --no-pager || true
    fi
  fi
fi

printf '\n'
printf 'Fuse is set up.\n'
if [ "$SERVICE_INSTALLED" -eq 1 ]; then
  if [ "$SERVICE_MODE" = "system" ]; then
    printf '  Logs:      journalctl -u fuse -f\n'
    printf '  Restart:   systemctl restart fuse\n'
  else
    printf '  Logs:      journalctl --user -u fuse -f\n'
    printf '  Restart:   systemctl --user restart fuse\n'
  fi
else
  printf '  Start it manually with:\n'
  printf '    ./start.sh\n'
fi
printf '  URL:       https://%s:%s  (self-signed cert: expect a browser warning)\n' "$ADDR" "$PORT_VALUE"
printf '  Config:    %s/.env\n' "$FUSE_HOME"
printf '  Data:      %s/data\n' "$FUSE_HOME"
printf '\n'
printf 'The first account registered becomes the admin.\n'
