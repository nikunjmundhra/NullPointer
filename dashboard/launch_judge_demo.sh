#!/usr/bin/env bash
set -e

# Change directory to repo root (parent of script directory)
cd "$(dirname "$0")/.."

echo ""
echo "============================================================="
echo "  AIRLENS INDIA - LIVE MODEL + DASHBOARD DEMO (macOS/Linux)"
echo "============================================================="
echo ""

# Determine Python 3 executable
if command -v python3 &>/dev/null; then
  PYTHON_EXE="python3"
elif command -v python &>/dev/null; then
  PYTHON_EXE="python"
else
  echo "Error: Python 3 was not found in PATH."
  echo "Please install Python 3 before running this script."
  exit 1
fi

# 1. Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
  echo "[1/4] Creating virtual environment (.venv)..."
  $PYTHON_EXE -m venv .venv
else
  echo "[1/4] Found existing virtual environment (.venv)."
fi

# 2. Install requirements
echo "[2/4] Installing/verifying requirements from requirements.txt..."
.venv/bin/python -m pip install -r requirements.txt

# 3. Run pipeline
echo ""
echo "[3/4] Running the AQI prediction pipeline..."
.venv/bin/python src/pipeline/run_pipeline.py

# 4. Start dashboard server & open browser
echo ""
echo "[4/4] Starting the local dashboard server..."
.venv/bin/python scripts/serve_dashboard.py &
SERVER_PID=$!

echo "Opening dashboard in your default browser..."
sleep 2

if command -v open &>/dev/null; then
  open "http://127.0.0.1:8000/dashboard/"
elif command -v xdg-open &>/dev/null; then
  xdg-open "http://127.0.0.1:8000/dashboard/"
fi

echo ""
echo "============================================================="
echo "Demo is ready! Server running on http://127.0.0.1:8000/dashboard/"
echo "Press Ctrl+C to stop the server."
echo "============================================================="

wait $SERVER_PID
