#!/bin/bash
set -e

echo "⚠️  PRODUCTION DEPLOYMENT"
read -p "Deploy to production? (yes/no): " confirm
[ "$confirm" != "yes" ] && echo "❌ Cancelled" && exit 0

echo "📦 Building image..."
DOCKER_BUILDKIT=1 docker buildx build \
  --platform linux/arm64 \
  -f Dockerfile.bot \
  -t adambehun/healthcare-bot:latest \
  --push . && echo "✅ Image built"

echo "🚀 Deploying to Pipecat Cloud..."
pipecat cloud deploy --force && echo "✅ Deployed: healthcare-voice-ai"
