#!/usr/bin/env bash
# Start kanban daemon with --executor agentao (REAL sub-agents) + Web UI.
# Caller MUST already have explicit user confirmation. Refuses to attach to
# an already-running daemon since the executor is fixed at daemon start.
# Env: BOARD (default workspace/board), PORT (default 8000).
set -euo pipefail

BOARD="${BOARD:-workspace/board}"
PORT="${PORT:-8000}"
URL="http://127.0.0.1:${PORT}/"

if [ ! -d "$BOARD" ]; then
  echo "error: $BOARD not found. Run 'uv run kanban init' first." >&2
  exit 1
fi

if uv run kanban daemon status 2>&1 | grep -E '^status:[[:space:]]+running' >/dev/null; then
  echo "error: daemon already running. Stop it first ('kanban-down.sh') — executor is fixed at start." >&2
  exit 2
fi

uv run kanban --executor agentao daemon --detach
echo "daemon: started (--executor agentao)"

PID_FILE="$BOARD/.web.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "web: already running (pid $(cat "$PID_FILE"))"
else
  [ -f "$PID_FILE" ] && rm -f "$PID_FILE"
  nohup uv run kanban web --host 127.0.0.1 --port "$PORT" \
    > "$BOARD/web.log" 2>&1 &
  echo $! > "$PID_FILE"
  echo "web: starting (pid $(cat "$PID_FILE"))"
fi

for _ in $(seq 1 60); do
  curl -fsS "$URL" >/dev/null 2>&1 && break
  sleep 0.5
done

if curl -fsS "$URL" >/dev/null 2>&1; then
  if   command -v open     >/dev/null 2>&1; then open     "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
  elif command -v wslview  >/dev/null 2>&1; then wslview  "$URL"
  else echo "open URL manually: $URL"
  fi
  echo "web: ready at $URL"
else
  echo "web: not responding after 30s — tail $BOARD/web.log" >&2
  exit 1
fi

uv run kanban daemon status
