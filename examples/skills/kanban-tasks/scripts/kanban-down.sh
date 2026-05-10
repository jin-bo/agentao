#!/usr/bin/env bash
# Stop Web UI then daemon (idempotent). Web first so it doesn't poll a
# detached board.
# Env: BOARD (default workspace/board).
set -euo pipefail

BOARD="${BOARD:-workspace/board}"
PID_FILE="$BOARD/.web.pid"

# 1) Web UI
if [ -f "$PID_FILE" ]; then
  PID=$(cat "$PID_FILE")
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    for _ in $(seq 1 10); do kill -0 "$PID" 2>/dev/null || break; sleep 0.2; done
    if kill -0 "$PID" 2>/dev/null; then
      kill -9 "$PID" 2>/dev/null || true
      echo "web: force-killed (pid $PID)"
    else
      echo "web: stopped (pid $PID)"
    fi
  else
    echo "web: pidfile present but process gone"
  fi
  rm -f "$PID_FILE"
else
  echo "web: not running"
fi

# 2) Daemon
if uv run kanban daemon status 2>&1 | grep -E '^status:[[:space:]]+running' >/dev/null; then
  uv run kanban daemon stop && echo "daemon: stop signal sent"
else
  echo "daemon: not running"
fi
