#!/usr/bin/env bash
set -euo pipefail
# если бот упал — поднять через 3 сек. для systemd лучше install-service.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
PY="$(find_python)"
echo "watch: $PY  cwd=$ROOT"
while true; do
  "$PY" bot.py || true
  echo "бот упал, через 3с снова"
  sleep 3
done
