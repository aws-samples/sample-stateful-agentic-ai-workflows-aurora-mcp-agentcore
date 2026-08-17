#!/bin/bash
# Meridian setup — Aurora schema + travel seed data

set -e

cd "$(dirname "$0")/.."

echo "Meridian setup"
echo "=============="
echo "Working directory: $(pwd)"
echo ""

echo "[1/8] Checking Python..."
python3 --version
echo ""

if [ ! -d "venv" ]; then
  echo "[2/8] Creating virtual environment..."
  python3 -m venv venv
else
  echo "[2/8] Virtual environment exists"
fi
echo ""

echo "[3/8] Activating venv..."
# shellcheck source=/dev/null
source venv/bin/activate
echo ""

echo "[4/8] Installing dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q --upgrade
echo ""

echo "[5/8] Verifying installation..."
python scripts/verify_installation.py
echo ""

if [ ! -f ".env" ]; then
  echo "[6/8] Creating .env from template..."
  cp .env.example .env
  echo "Edit .env with Aurora ARNs before continuing."
  read -r -p "Press Enter after updating .env..."
else
  echo "[6/8] .env exists"
fi
echo ""

echo "[7/8] Testing Aurora connection..."
if python scripts/test_aurora_connection.py; then
  echo ""
  echo "[8/8] Initializing schema and seeding trip_packages..."
  python scripts/init_aurora_schema.py
  python scripts/seed_data.py
  echo ""
  echo "Setup complete. Start backend:"
  echo "  uvicorn backend.main:app --reload --port 8000"
  echo "Start frontend:"
  echo "  cd frontend && npm install && npm run dev"
else
  echo ""
  echo "Aurora connection failed — check .env and cluster status."
  exit 1
fi
