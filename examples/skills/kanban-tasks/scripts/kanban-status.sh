#!/usr/bin/env bash
# Combined snapshot: board / daemon / web UI / cards.
# Env: BOARD (default workspace/board).
set -uo pipefail

BOARD="${BOARD:-workspace/board}"

echo "== board =="
if [ -d "$BOARD" ]; then
  echo "  path: $BOARD"
else
  echo "  not initialized — run 'uv run kanban init'"
  exit 1
fi

echo
echo "== daemon =="
uv run kanban daemon status 2>&1 | sed 's/^/  /'

echo
echo "== web =="
PID_FILE="$BOARD/.web.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "  running pid=$(cat "$PID_FILE") log=$BOARD/web.log"
else
  echo "  not running"
  [ -f "$PID_FILE" ] && rm -f "$PID_FILE"
fi

echo
echo "== cards (head) =="
uv run kanban list 2>&1 | sed 's/^/  /' | head -40
