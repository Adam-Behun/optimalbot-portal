#!/bin/bash
set -e

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
IMAGE_TAG="${TIMESTAMP}"

echo "🧪 TEST DEPLOYMENT"

# Verify required files exist
if [ ! -f "uv.lock" ]; then
    echo "❌ uv.lock not found. Run: ./update-bot-deps.sh"
    exit 1
fi

if [ ! -f "pyproject.bot.toml" ]; then
    echo "❌ pyproject.bot.toml not found"
    exit 1
fi

echo "📦 Building image: bot:${IMAGE_TAG}..."
DOCKER_BUILDKIT=1 docker buildx build \
  --platform linux/arm64 \
  --no-cache \
  -f Dockerfile.bot \
  -t adambehun/bot:${IMAGE_TAG} \
  --push .

echo "✅ Image built"

# Swap in test config with updated image tag
mv pcc-deploy.toml pcc-deploy.toml.backup 2>/dev/null || true
cp pcc-deploy.test.toml pcc-deploy.toml
sed -i "s|image = \".*\"|image = \"adambehun/bot:${IMAGE_TAG}\"|" pcc-deploy.toml

echo "🚀 Deploying to Pipecat Cloud..."
pipecat cloud deploy --force

echo "✅ Deployed: test (bot:${IMAGE_TAG})"

# Restore prod config
rm pcc-deploy.toml
mv pcc-deploy.toml.backup pcc-deploy.toml 2>/dev/null || true
