#!/usr/bin/env bash
# Installs Personal Cloud OS on a Linux server (VPS) as a hardened systemd service.
#
#   sudo bash scripts/install-linux.sh                 # install / upgrade
#   sudo bash scripts/install-linux.sh --link CODE     # ...and link it to your cloud dashboard
#
# Safe to re-run: it upgrades the code and dependencies, keeps /etc/pcloud.env and all data.
# The agent runs as the unprivileged system user "pcloud", listens on 127.0.0.1 only, and can
# write only to /var/lib/pcloud (its data) and /srv/pcloud (default storage folder).
set -euo pipefail

APP_DIR=/opt/personal-cloud
DATA_DIR=/var/lib/pcloud
STORAGE_DIR=/srv/pcloud
ENV_FILE=/etc/pcloud.env
UNIT_FILE=/etc/systemd/system/pcloud.service
SVC_USER=pcloud
VENV="$APP_DIR/server/.venv"
PY="$VENV/bin/python"

LINK_CODE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --link) LINK_CODE="${2:-}"; [[ -n "$LINK_CODE" ]] || { echo "--link needs a code" >&2; exit 2; }; shift 2 ;;
    --link=*) LINK_CODE="${1#--link=}"; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m%s\033[0m\n' "$*"; }
warn() { printf '    \033[33m%s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Linux" ]] || die "This installer is for Linux. On Windows use scripts\\install.ps1."
[[ $EUID -eq 0 ]] || die "Run with sudo: sudo bash $0 $*"
command -v systemctl >/dev/null || die "systemd is required."

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$SRC_DIR/server/requirements.txt" ]] || die "Cannot find server/requirements.txt next to this script."

# ------------------------------------------------------------------ packages
step "Checking Python 3.10+"
if command -v apt-get >/dev/null; then
  if ! python3 -c 'import venv, ensurepip' >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip >/dev/null
  fi
elif command -v dnf >/dev/null; then
  python3 -c 'import venv, ensurepip' >/dev/null 2>&1 || dnf install -y -q python3 python3-pip >/dev/null
fi
command -v python3 >/dev/null || die "python3 not found. Install Python 3.10 or newer and re-run."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python $(python3 -V 2>&1) is too old; 3.10 or newer is required."
ok "$(python3 -V 2>&1)"

# ---------------------------------------------------------------- user
step "Creating system user '$SVC_USER'"
if id "$SVC_USER" >/dev/null 2>&1; then
  ok "already exists"
else
  NOLOGIN="$(command -v nologin || echo /usr/sbin/nologin)"
  useradd --system --home-dir "$DATA_DIR" --no-create-home --shell "$NOLOGIN" --user-group "$SVC_USER"
  ok "created (no login shell)"
fi

# ---------------------------------------------------------------- code
step "Installing application to $APP_DIR"
mkdir -p "$APP_DIR"
if [[ "$SRC_DIR" != "$APP_DIR" ]]; then
  tar -C "$SRC_DIR" --exclude=./.git --exclude=./server/.venv --exclude=./web/node_modules \
      --exclude=./.env --exclude='__pycache__' -cf - . | tar -C "$APP_DIR" -xf -
  ok "copied from $SRC_DIR"
else
  ok "using repository in place"
fi
chown -R root:root "$APP_DIR"
chmod -R u=rwX,go=rX "$APP_DIR"
[[ -d "$APP_DIR/web/out" ]] || warn "web/out (built dashboard) is missing: the API works, but build the dashboard with 'npm ci && npm run build' in web/ to get the local UI."

step "Creating virtual environment and installing dependencies"
[[ -x "$PY" ]] || python3 -m venv "$VENV"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r "$APP_DIR/server/requirements.txt"
"$PY" -m compileall -q "$APP_DIR/server/pcloud" >/dev/null || true
ok "dependencies installed"

# ---------------------------------------------------------------- folders
step "Creating data and storage folders"
install -d -m 0750 -o "$SVC_USER" -g "$SVC_USER" "$DATA_DIR"
install -d -m 0750 -o "$SVC_USER" -g "$SVC_USER" "$STORAGE_DIR"
ok "$DATA_DIR (agent data, mode 0750)"
ok "$STORAGE_DIR (default storage folder)"

# ---------------------------------------------------------------- config
step "Writing $ENV_FILE"
if [[ -f "$ENV_FILE" ]]; then
  ok "kept existing file"
else
  cat >"$ENV_FILE" <<EOF
# Personal Cloud OS agent settings (read by pcloud.service). Restart after editing:
#   sudo systemctl restart pcloud
PCLOUD_HOST=127.0.0.1
PCLOUD_PORT=8787
PCLOUD_DATA_DIR=$DATA_DIR
EOF
  ok "created"
fi
chown root:"$SVC_USER" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

step "Installing systemd unit $UNIT_FILE"
cat >"$UNIT_FILE" <<EOF
[Unit]
Description=Personal Cloud OS agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC_USER
Group=$SVC_USER
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WorkingDirectory=$APP_DIR/server
ExecStart=$PY -m pcloud.cli run
Restart=always
RestartSec=5
UMask=0027

# Hardening: unprivileged, read-only OS, writable only where the agent keeps data.
# To share another folder, add it with: sudo systemctl edit pcloud
#   [Service]
#   ReadWritePaths=/path/to/folder
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=$DATA_DIR $STORAGE_DIR
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictRealtime=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable pcloud.service >/dev/null 2>&1
systemctl restart pcloud.service
ok "pcloud.service enabled and (re)started"

# ---------------------------------------------------------------- link
# Run a pcloud CLI command as the service user with the service's environment.
run_as_pcloud() {
  local envv=()
  mapfile -t envv < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE")
  (cd "$APP_DIR/server" && runuser -u "$SVC_USER" -- env "${envv[@]}" PYTHONDONTWRITEBYTECODE=1 "$PY" -m pcloud.cli "$@")
}

if [[ -n "$LINK_CODE" ]]; then
  step "Linking this server to your cloud dashboard"
  if run_as_pcloud link "$LINK_CODE"; then
    systemctl restart pcloud.service
    ok "linked"
  else
    warn "Linking failed. Retry later with: sudo bash $0 --link <code>"
  fi
fi

# ---------------------------------------------------------------- status
step "Status"
for _ in $(seq 1 15); do
  systemctl is-active --quiet pcloud.service && break
  sleep 1
done
if systemctl is-active --quiet pcloud.service; then
  ok "pcloud.service is running"
else
  warn "pcloud.service is not running yet. Check: journalctl -u pcloud -n 50"
fi
PORT="$(grep -E '^PCLOUD_PORT=' "$ENV_FILE" | cut -d= -f2)"
PORT="${PORT:-8787}"
SETUP_CODE="$(run_as_pcloud setup-code 2>/dev/null | tail -n1 || true)"

cat <<EOF

Next steps
  * The agent listens on 127.0.0.1:$PORT only; nothing is exposed to the internet.
  * Reach it privately with Tailscale:  tailscale serve --bg http://127.0.0.1:$PORT
    or from your PC with an SSH tunnel: ssh -L $PORT:127.0.0.1:$PORT <user>@<this-server>
  * First-time setup code: ${SETUP_CODE:-run 'sudo bash $APP_DIR/scripts/pcloud-linux.sh doctor'}
  * Put files in $STORAGE_DIR and add it as a storage location in the dashboard.
  * Manage the service: sudo bash $APP_DIR/scripts/pcloud-linux.sh status|logs|restart|doctor|uninstall
EOF
[[ -n "$LINK_CODE" ]] || echo "  * Link to your cloud dashboard: sudo bash $APP_DIR/scripts/install-linux.sh --link <code>"
