#!/bin/bash
set -e

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
IMAGE_TAG="test-${TIMESTAMP}"

echo "🧪 TEST DEPLOYMENT"

# Verify required files exist
if [ ! -f "uv.lock" ]; then
    echo "❌ uv.lock not found. Generate it with:"
    echo "   cp pyproject.bot.toml pyproject.toml && uv lock && rm pyproject.toml"
    exit 1
fi

if [ ! -f "pyproject.bot.toml" ]; then
    echo "❌ pyproject.bot.toml not found"
    exit 1
fi

echo "📦 Building image: ${IMAGE_TAG}..."
DOCKER_BUILDKIT=1 docker buildx build \
  --platform linux/arm64 \
  --no-cache \
  -f Dockerfile.bot \
  -t adambehun/healthcare-bot:${IMAGE_TAG} \
  -t adambehun/healthcare-bot:test \
  --push . && echo "✅ Image built"

mv pcc-deploy.toml pcc-deploy.toml.backup 2>/dev/null || true
cp pcc-deploy.test.toml pcc-deploy.toml
sed -i "s|image = \".*\"|image = \"adambehun/healthcare-bot:${IMAGE_TAG}\"|" pcc-deploy.toml

echo "🚀 Deploying to Pipecat Cloud..."
pipecat cloud deploy --force && echo "✅ Deployed: healthcare-voice-ai-test (${IMAGE_TAG})"

rm pcc-deploy.toml
mv pcc-deploy.toml.backup pcc-deploy.toml 2>/dev/null || true
