#!/bin/bash
set -e

read -p "Deploy to production? (yes/no): " confirm
[ "$confirm" != "yes" ] && echo "Cancelled" && exit 0

[ ! -f "uv.lock" ] && echo "uv.lock not found. Run: ./update-bot-deps.sh" && exit 1
[ ! -f "pyproject.bot.toml" ] && echo "pyproject.bot.toml not found" && exit 1

DOCKER_BUILDKIT=1 docker buildx build \
  --platform linux/arm64 \
  -f Dockerfile.bot \
  -t adambehun/bot:latest \
  --push .

pipecat cloud deploy --force

echo "Deployed: prod (bot:latest)"
