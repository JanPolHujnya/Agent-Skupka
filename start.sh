#!/usr/bin/env bash
set -euo pipefail
# запуск в консоли. ctrl+c останавливает.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
PY="$(find_python)"
echo "бот стартует ($PY). в телеге нажми /start. не закрывай терминал."
exec "$PY" bot.py
