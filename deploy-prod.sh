#!/bin/bash
set -e

echo "⚠️  PRODUCTION DEPLOYMENT"
read -p "Deploy to production? (yes/no): " confirm
[ "$confirm" != "yes" ] && echo "❌ Cancelled" && exit 0

# Verify required files exist
if [ ! -f "uv.lock" ]; then
    echo "❌ uv.lock not found. Run: ./update-bot-deps.sh"
    exit 1
fi

if [ ! -f "pyproject.bot.toml" ]; then
    echo "❌ pyproject.bot.toml not found"
    exit 1
fi

echo "📦 Building image: bot:latest..."
DOCKER_BUILDKIT=1 docker buildx build \
  --platform linux/arm64 \
  -f Dockerfile.bot \
  -t adambehun/bot:latest \
  --push .

echo "✅ Image built"

echo "🚀 Deploying to Pipecat Cloud..."
pipecat cloud deploy --force

echo "✅ Deployed: prod (bot:latest)"
