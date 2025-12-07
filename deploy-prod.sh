#!/bin/bash
set -e

read -p "Deploy to production? (yes/no): " confirm
[ "$confirm" != "yes" ] && echo "Cancelled" && exit 0

[ ! -f "uv.bot.lock" ] && echo "uv.bot.lock not found. Run: ./update-bot-deps.sh" && exit 1
[ ! -f "pyproject.bot.toml" ] && echo "pyproject.bot.toml not found" && exit 1

echo "Running bot validation..."
python -c "
from bot_validation import validate_all_configs
valid, errors = validate_all_configs()
if not valid:
    for e in errors: print(f'  ERROR: {e}')
    exit(1)
print('All configs valid')
" || { echo "Bot validation failed"; exit 1; }

DOCKER_BUILDKIT=1 docker buildx build \
  --platform linux/arm64 \
  -f Dockerfile.bot \
  -t adambehun/bot:latest \
  --push .

pipecat cloud deploy --force

echo "Deployed: prod (bot:latest)"
