#!/usr/bin/env bash
set -euo pipefail
# в фон, через сторож. на сервере лучше systemd: sudo ./install-service.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
find_python >/dev/null
stop_here
nohup "$ROOT/watch.sh" >>"$ROOT/data/watch.log" 2>&1 &
sleep 2
pids="$(bot_pids | tr '\n' ' ')"
if [ -n "${pids% }" ]; then
  echo "running pid=$pids"
else
  echo "STILL_DOWN  глянь data/watch.log и data/bot.log"
  exit 1
fi
