#!/usr/bin/env bash
# Start the POC: FastAPI product catalogue + Streamlit chatbot.
# Ollama must already be running; Mem0 Cloud is accessed remotely via MCP.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "No .venv found. Set it up with: uv sync"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [ ! -f ".env" ]; then
  echo "WARNING: .env not found. Copy .env.example to .env and set MEM0_API_KEY."
fi

if ! curl -sf --max-time 3 "${PRODUCT_API_URL:-http://localhost:8000}/health" >/dev/null 2>&1; then
  echo "Starting product API on http://localhost:8000 ..."
  uvicorn backend.api:app --port 8000 &
  API_PID=$!
  trap 'kill ${API_PID} 2>/dev/null || true' EXIT INT TERM
  sleep 2
else
  echo "Product API already running on http://localhost:8000"
fi

echo "Starting Streamlit (Ctrl+C stops the Streamlit app and the product API it launched)..."
streamlit run app/ui/streamlit_app.py
