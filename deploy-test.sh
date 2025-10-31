#!/bin/bash
# Deploy bot to TEST environment on Pipecat Cloud

set -e

echo "🧪 ============================================"
echo "   Deploying to TEST Environment"
echo "============================================"
echo ""

# Build Docker image for ARM64 (Pipecat Cloud architecture)
# BuildKit automatically uses Dockerfile.bot.dockerignore
# Generate unique tag with timestamp to force fresh pull
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
IMAGE_TAG="test-${TIMESTAMP}"

echo "📦 Building optimized Docker image (tag: ${IMAGE_TAG})..."
DOCKER_BUILDKIT=1 docker buildx build \
  --platform linux/arm64 \
  --no-cache \
  -f Dockerfile.bot \
  -t adambehun/healthcare-bot:${IMAGE_TAG} \
  -t adambehun/healthcare-bot:test \
  --push \
  .

echo ""
echo "🚀 Deploying to Pipecat Cloud (test agent)..."
# Temporarily use test config (CLI only reads pcc-deploy.toml)
mv pcc-deploy.toml pcc-deploy.toml.backup 2>/dev/null || true
cp pcc-deploy.test.toml pcc-deploy.toml

# Update the image tag in pcc-deploy.toml to use the timestamped version
sed -i "s|image = \".*\"|image = \"adambehun/healthcare-bot:${IMAGE_TAG}\"|" pcc-deploy.toml

echo "Using image: adambehun/healthcare-bot:${IMAGE_TAG}"
pipecat cloud deploy --force

# Restore production config
rm pcc-deploy.toml
mv pcc-deploy.toml.backup pcc-deploy.toml 2>/dev/null || true

echo ""
echo "✅ ============================================"
echo "   Test Deployment Complete!"
echo "============================================"
echo ""
echo "📋 Agent Name: healthcare-voice-ai-test"
echo "🏷️  Image Tag: adambehun/healthcare-bot:${IMAGE_TAG}"
echo "🏷️  Also tagged as: adambehun/healthcare-bot:test"
echo ""
echo "Next steps:"
echo "1. Verify .env: PIPECAT_AGENT_NAME=healthcare-voice-ai-test"
echo "2. Start call via frontend at http://localhost:3000"
echo "3. Check Pipecat Cloud logs for: '✅ Pipeline built successfully'"
echo ""
