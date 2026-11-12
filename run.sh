#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "A preparar o ambiente pela primeira vez…"
  uv sync
fi

HOST="${RAIZ_HOST:-127.0.0.1}"
PORT="${RAIZ_PORT:-8000}"

echo ""
echo "  raiz · finanças com raízes"
echo "  http://${HOST}:${PORT}"
echo ""

if command -v xdg-open >/dev/null 2>&1; then
  (sleep 1; xdg-open "http://${HOST}:${PORT}" >/dev/null 2>&1 || true) &
fi

ARGS=()
if [ "${RAIZ_RELOAD:-0}" = "1" ]; then
  ARGS+=(--reload)
fi

exec .venv/bin/uvicorn app.main:app --host "${HOST}" --port "${PORT}" "${ARGS[@]}"
