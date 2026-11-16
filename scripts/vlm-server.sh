#!/usr/bin/env bash
# Start/stop the local MLX-VLM server used by ocr_scanned_page.py (step 2).
#
#   scripts/vlm-server.sh start     # start in the background, preload the model, wait until ready
#   scripts/vlm-server.sh stop      # stop the server started by this script
#   scripts/vlm-server.sh restart
#   scripts/vlm-server.sh status
#   scripts/vlm-server.sh log       # follow the server log
#
# mlx_vlm.server is a generic OpenAI-compatible server for vision-language models on Apple
# Silicon (package mlx-vlm); this wrapper runs it from the project venv, bound to localhost,
# with the PaddleOCR-VL model preloaded. State (pid, log) lives in .run/ (git-ignored).
#
# Environment overrides: VLM_PORT (8111), VLM_HOST (127.0.0.1), VLM_MODEL, VLM_START_TIMEOUT (300 s)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${VLM_PORT:-8111}"
HOST="${VLM_HOST:-127.0.0.1}"
MODEL="${VLM_MODEL:-PaddlePaddle/PaddleOCR-VL-1.6}"
TIMEOUT="${VLM_START_TIMEOUT:-300}"
RUN="$ROOT/.run"
PIDFILE="$RUN/vlm-server.pid"
LOG="$RUN/vlm-server.log"
SERVER="$ROOT/.venv/bin/mlx_vlm.server"

port_open() { nc -z "$HOST" "$PORT" >/dev/null 2>&1; }
our_pid() { [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null && cat "$PIDFILE"; }
listener() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1; }

start() {
  if pid=$(our_pid); then echo "already running (pid $pid) on $HOST:$PORT"; return 0; fi
  if port_open || [[ -n "$(listener)" ]]; then
    other=$(listener)
    echo "error: port $PORT is already in use by another process (pid ${other:-?}):" >&2
    [[ -n "$other" ]] && ps -o pid=,command= -p "$other" >&2
    echo "stop it first (e.g. Ctrl-C in its terminal, or: kill $other)" >&2
    return 1
  fi
  [[ -x "$SERVER" ]] || { echo "error: $SERVER not found; run: uv sync" >&2; return 1; }
  mkdir -p "$RUN"
  echo "starting mlx_vlm.server on $HOST:$PORT with $MODEL (log: ${LOG#$ROOT/})"
  nohup "$SERVER" --host "$HOST" --port "$PORT" --model "$MODEL" >>"$LOG" 2>&1 &
  echo $! >"$PIDFILE"
  for ((i = 0; i < TIMEOUT; i++)); do
    if port_open; then echo "ready (pid $(cat "$PIDFILE"))"; return 0; fi
    if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "error: server exited during start; last log lines:" >&2
      tail -20 "$LOG" >&2
      rm -f "$PIDFILE"
      return 1
    fi
    sleep 1
  done
  echo "error: not listening after ${TIMEOUT}s (first start downloads the model); see: $0 log" >&2
  return 1
}

stop() {
  if ! pid=$(our_pid); then
    rm -f "$PIDFILE"
    if other=$(listener) && [[ -n "$other" ]]; then
      echo "not started by this script; port $PORT is used by pid $other — leaving it alone:"
      ps -o pid=,command= -p "$other"
    else
      echo "not running"
    fi
    return 0
  fi
  kill "$pid"
  for ((i = 0; i < 20; i++)); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  if kill -0 "$pid" 2>/dev/null; then kill -9 "$pid"; fi
  rm -f "$PIDFILE"
  echo "stopped (pid $pid)"
}

status() {
  if pid=$(our_pid); then
    state=$(port_open && echo "listening" || echo "starting")
    echo "running (pid $pid), $state on $HOST:$PORT, model $MODEL"
  elif other=$(listener) && [[ -n "$other" ]]; then
    echo "port $PORT in use by a server not started by this script (pid $other):"
    ps -o pid=,command= -p "$other"
  else
    echo "not running"
    return 3
  fi
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  log) touch "$LOG" 2>/dev/null || mkdir -p "$RUN"; tail -n 50 -f "$LOG" ;;
  *) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
