#!/usr/bin/env bash
# Manage the Personal Cloud OS agent installed by scripts/install-linux.sh.
#
#   sudo bash scripts/pcloud-linux.sh status | logs | restart | doctor | uninstall
set -euo pipefail

APP_DIR=/opt/personal-cloud
DATA_DIR=/var/lib/pcloud
ENV_FILE=/etc/pcloud.env
UNIT_FILE=/etc/systemd/system/pcloud.service
SVC_USER=pcloud
VENV="$APP_DIR/server/.venv"
PY="$VENV/bin/python"

usage() { sed -n '2,4p' "$0"; }
need_root() { [[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo bash $0 ${1:-}" >&2; exit 1; }; }
confirm() { local a; read -r -p "$1 [y/N] " a || true; [[ "$a" =~ ^[Yy]([Ee][Ss])?$ ]]; }

run_as_pcloud() {
  local envv=()
  [[ -f "$ENV_FILE" ]] && mapfile -t envv < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE")
  (cd "$APP_DIR/server" && runuser -u "$SVC_USER" -- env "${envv[@]}" PYTHONDONTWRITEBYTECODE=1 "$PY" -m pcloud.cli "$@")
}

cmd_status() {
  systemctl --no-pager status pcloud.service || true
}

cmd_logs() {
  exec journalctl -u pcloud -f
}

cmd_restart() {
  need_root restart
  systemctl restart pcloud.service
  sleep 2
  systemctl is-active --quiet pcloud.service && echo "pcloud.service restarted." \
    || { echo "pcloud.service failed to start:"; journalctl -u pcloud -n 30 --no-pager; exit 1; }
}

cmd_doctor() {
  need_root doctor
  local rc=0
  echo "== Service"
  if systemctl is-active --quiet pcloud.service; then echo "[ OK ] pcloud.service is running"
  else echo "[FAIL] pcloud.service is not running (journalctl -u pcloud -n 50)"; rc=1; fi
  systemctl is-enabled --quiet pcloud.service && echo "[ OK ] starts at boot" || echo "[WARN] not enabled at boot"
  echo "== Listening address"
  local port; port="$(grep -E '^PCLOUD_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)"; port="${port:-8787}"
  if command -v ss >/dev/null; then
    local listen addr public=0
    listen="$(ss -Hltn "sport = :$port" 2>/dev/null | awk '{print $4}' | sort -u | tr '\n' ' ')"
    for addr in $listen; do [[ "$addr" == 127.0.0.1:* || "$addr" == "[::1]:"* ]] || public=1; done
    if [[ -z "$listen" ]]; then echo "[WARN] nothing listening on port $port"
    elif [[ $public -eq 1 ]]; then echo "[WARN] listening on $listen- not loopback only. Set PCLOUD_HOST=127.0.0.1 in $ENV_FILE"
    else echo "[ OK ] $listen(loopback only)"; fi
  fi
  echo "== Writable folders (systemd ReadWritePaths)"
  echo "       $(systemctl show -p ReadWritePaths --value pcloud.service 2>/dev/null)"
  echo "       Storage folders outside these paths are read-only to the agent; add them with 'systemctl edit pcloud'."
  echo "== Tailscale"
  if command -v tailscale >/dev/null; then tailscale status --self=true --peers=false 2>/dev/null | head -n 3 || echo "[WARN] tailscale not connected"
  else echo "[INFO] tailscale not installed (https://tailscale.com/download/linux)"; fi
  echo "== Agent self-check"
  run_as_pcloud doctor || rc=1
  echo "== Setup"
  run_as_pcloud setup-code 2>/dev/null | tail -n1 | sed 's/^/       setup code: /' || true
  exit "$rc"
}

cmd_uninstall() {
  need_root uninstall
  echo "This removes the pcloud service and its Python environment."
  echo "Storage folders (such as /srv/pcloud) are never deleted."
  confirm "Continue?" || { echo "Cancelled."; exit 0; }
  if systemctl list-unit-files pcloud.service >/dev/null 2>&1; then
    systemctl disable --now pcloud.service 2>/dev/null || true
  fi
  rm -f "$UNIT_FILE"
  rm -rf /etc/systemd/system/pcloud.service.d
  systemctl daemon-reload
  echo "Removed systemd unit."
  if [[ -d "$VENV" ]]; then rm -rf "$VENV"; echo "Removed $VENV."; fi
  if [[ -d "$DATA_DIR" ]]; then
    if confirm "Also delete agent data in $DATA_DIR (accounts, settings, trash, versions)? This cannot be undone."; then
      rm -rf "$DATA_DIR"
      echo "Deleted $DATA_DIR."
    else
      echo "Kept $DATA_DIR."
    fi
  fi
  echo "Kept $ENV_FILE, the '$SVC_USER' user and $APP_DIR source files (remove them manually if you wish:"
  echo "  sudo rm -f $ENV_FILE; sudo userdel $SVC_USER; sudo rm -rf $APP_DIR)"
}

case "${1:-}" in
  status) cmd_status ;;
  logs) cmd_logs ;;
  restart) cmd_restart ;;
  doctor) cmd_doctor ;;
  uninstall) cmd_uninstall ;;
  -h|--help|help|"") usage ;;
  *) echo "Unknown command: $1" >&2; usage; exit 2 ;;
esac
