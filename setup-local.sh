#!/bin/bash
set -e

if ! command -v uv &> /dev/null; then
    echo "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

[ -d ".venv" ] && rm -rf .venv

uv lock
mv uv.lock uv.local.lock
cp uv.local.lock uv.lock
uv sync
rm uv.lock

echo "Done. Run: source .venv/bin/activate"
