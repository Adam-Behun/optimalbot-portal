#!/bin/bash
set -e

# =============================================================================
# Local Development Setup
# Creates venv and generates lockfiles for all environments
# =============================================================================

if ! command -v uv &> /dev/null; then
    echo "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo "Setting up local development environment..."

# Clean existing venv
[ -d ".venv" ] && rm -rf .venv

# Generate lockfiles for all environments (with latest versions)
echo "Generating lockfiles..."

# Local dev (full: backend + bot)
uv lock --upgrade
cp uv.lock uv.local.lock

# API/Backend only
cp pyproject.api.toml pyproject.toml.bak
mv pyproject.toml pyproject.toml.main
cp pyproject.api.toml pyproject.toml
uv lock --upgrade
mv uv.lock uv.api.lock
mv pyproject.toml.main pyproject.toml
rm pyproject.toml.bak

# Bot only
cp pyproject.bot.toml pyproject.toml.bak
mv pyproject.toml pyproject.toml.main
cp pyproject.bot.toml pyproject.toml
uv lock --upgrade
mv uv.lock uv.bot.lock
mv pyproject.toml.main pyproject.toml
rm pyproject.toml.bak

# Install local dev environment
echo "Installing dependencies..."
cp uv.local.lock uv.lock
uv sync
rm uv.lock

echo ""
echo "Done! Generated lockfiles:"
echo "  - uv.local.lock (local dev: backend + bot)"
echo "  - uv.api.lock   (backend deployment)"
echo "  - uv.bot.lock   (bot deployment)"
echo ""
echo "Run: source .venv/bin/activate"
