#!/usr/bin/env bash
set -euo pipefail
# systemd. на впс:
#   sudo ./install-service.sh
# без рута, пока сессия жива (или linger):
#   ./install-service.sh --user
# снять:
#   sudo ./install-service.sh --remove
#   ./install-service.sh --user --remove

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
PY="$(find_python)"
PY_PATH="$(command -v "$PY")"
NAME="uchetskup-bot"
USER_NAME="${SUDO_USER:-$(id -un)}"
GROUP_NAME="$(id -gn "$USER_NAME")"
MODE="system"
ACTION="install"

for arg in "$@"; do
  case "$arg" in
    --user) MODE="user" ;;
    --remove|--uninstall) ACTION="remove" ;;
    -h|--help)
      echo "usage: $0 [--user] [--remove]"
      exit 0
      ;;
    *)
      echo "не знаю флаг: $arg"
      exit 1
      ;;
  esac
done

if [ ! -f "$ROOT/.env" ]; then
  echo "нет .env — скопируй .env.example и заполни"
  exit 1
fi

unit_body() {
  local wanted="$1"
  local user_line=""
  if [ "$MODE" = "system" ]; then
    user_line="User=${USER_NAME}
Group=${GROUP_NAME}"
  fi
  cat <<EOF
[Unit]
Description=Учёт железа Telegram-бот
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
${user_line}
WorkingDirectory=${ROOT}
ExecStart=${PY_PATH} ${ROOT}/bot.py
Restart=always
RestartSec=3
TimeoutStopSec=20
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=true

[Install]
WantedBy=${wanted}
EOF
}

if [ "$MODE" = "user" ]; then
  UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  UNIT_PATH="${UNIT_DIR}/${NAME}.service"
  CTL=(systemctl --user)
  WANTED="default.target"
else
  if [ "$(id -u)" -ne 0 ]; then
    echo "для системного сервиса нужен sudo, либо так: $0 --user"
    exit 1
  fi
  UNIT_DIR="/etc/systemd/system"
  UNIT_PATH="${UNIT_DIR}/${NAME}.service"
  CTL=(systemctl)
  WANTED="multi-user.target"
fi

if [ "$ACTION" = "remove" ]; then
  "${CTL[@]}" disable --now "$NAME" 2>/dev/null || true
  rm -f "$UNIT_PATH"
  "${CTL[@]}" daemon-reload
  echo "сервис ${NAME} снят"
  exit 0
fi

mkdir -p "$UNIT_DIR"
unit_body "$WANTED" >"$UNIT_PATH"
"${CTL[@]}" daemon-reload
"${CTL[@]}" enable --now "$NAME"

if [ "$MODE" = "user" ]; then
  if command -v loginctl >/dev/null 2>&1; then
    loginctl enable-linger "$USER_NAME" 2>/dev/null || true
  fi
  echo "user-сервис ${NAME} включён. логи: journalctl --user -u ${NAME} -f"
else
  echo "сервис ${NAME} включён. логи: journalctl -u ${NAME} -f"
fi

sleep 2
if "${CTL[@]}" is-active --quiet "$NAME"; then
  echo "active"
else
  echo "не поднялся, логи сервиса:"
  "${CTL[@]}" --no-pager -l status "$NAME" || true
  exit 1
fi
