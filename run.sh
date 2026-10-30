#!/usr/bin/env bash
# ShowMe on macOS/Linux: sets up a virtual environment once, then starts the app on port 8000.
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
. .venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Created .env. Add your MODEL_API_KEY, then run again (mock mode for now)."; }
exec python backend/scripts/serve.py
