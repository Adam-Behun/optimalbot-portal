#!/bin/bash
# Deploy bot to PRODUCTION environment on Pipecat Cloud

set -e

echo "🚀 ============================================"
echo "   PRODUCTION DEPLOYMENT"
echo "============================================"
echo ""
echo "⚠️  WARNING: This will update the PRODUCTION agent!"
echo ""
read -p "Are you sure you want to deploy to production? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "❌ Deployment cancelled."
    exit 0
fi

echo ""

# Build Docker image for ARM64
echo "📦 Building Docker image (latest tag)..."
docker buildx build \
  --platform linux/arm64 \
  -f Dockerfile.bot \
  -t adambehun/healthcare-bot:latest \
  --push \
  .

echo ""
echo "🚀 Deploying to Pipecat Cloud (production agent)..."
pipecatcloud deploy -f pcc-deploy.toml

echo ""
echo "✅ ============================================"
echo "   Production Deployment Complete!"
echo "============================================"
echo ""
echo "📋 Agent Name: healthcare-voice-ai"
echo "🏷️  Image Tag: adambehun/healthcare-bot:latest"
echo ""
echo "Next steps:"
echo "1. Update .env: PIPECAT_AGENT_NAME=healthcare-voice-ai"
echo "2. Restart backend if needed"
echo "3. Monitor logs: pipecatcloud agent logs healthcare-voice-ai"
echo ""
