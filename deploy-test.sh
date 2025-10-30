#!/bin/bash
# Deploy bot to TEST environment on Pipecat Cloud

set -e

echo "🧪 ============================================"
echo "   Deploying to TEST Environment"
echo "============================================"
echo ""

# Build Docker image for ARM64 (Pipecat Cloud architecture)
# BuildKit automatically uses Dockerfile.bot.dockerignore
echo "📦 Building optimized Docker image (test tag)..."
DOCKER_BUILDKIT=1 docker buildx build \
  --platform linux/arm64 \
  -f Dockerfile.bot \
  -t adambehun/healthcare-bot:test \
  --push \
  .

echo ""
echo "🚀 Deploying to Pipecat Cloud (test agent)..."
# Temporarily use test config (CLI only reads pcc-deploy.toml)
mv pcc-deploy.toml pcc-deploy.toml.backup 2>/dev/null || true
cp pcc-deploy.test.toml pcc-deploy.toml
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
echo "🏷️  Image Tag: adambehun/healthcare-bot:test"
echo ""
echo "Next steps:"
echo "1. Update .env: PIPECAT_AGENT_NAME=healthcare-voice-ai-test"
echo "2. Restart backend: python app.py"
echo "3. Test with real call via frontend"
echo ""
