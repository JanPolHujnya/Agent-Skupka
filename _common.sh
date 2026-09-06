#!/usr/bin/env bash
# общее для start/watch/launch на линуксе. не запускать само.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/data"
export PYTHONUNBUFFERED=1

find_python() {
  local c
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        printf '%s\n' "$c"
        return 0
      fi
    fi
  done
  echo "нужен Python 3.10+  (поставь python3)" >&2
  return 1
}

# только процессы этого каталога
stop_here() {
  local proc pid cwd cmd
  for proc in /proc/[0-9]*; do
    pid="${proc#/proc/}"
    cwd="$(readlink "$proc/cwd" 2>/dev/null)" || continue
    [ "$cwd" = "$ROOT" ] || continue
    cmd="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null)" || continue
    case "$cmd" in
      *bot.py*|*watch.sh*|*start.sh*)
        kill "$pid" 2>/dev/null || true
        ;;
    esac
  done
  sleep 0.5
}

bot_pids() {
  local proc pid cwd cmd
  for proc in /proc/[0-9]*; do
    pid="${proc#/proc/}"
    cwd="$(readlink "$proc/cwd" 2>/dev/null)" || continue
    [ "$cwd" = "$ROOT" ] || continue
    cmd="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null)" || continue
    case "$cmd" in
      *bot.py*) printf '%s\n' "$pid" ;;
    esac
  done
}
