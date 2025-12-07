#!/bin/bash
set -e

[ ! -f "uv.lock" ] && echo "uv.lock not found. Run: ./update-bot-deps.sh" && exit 1
[ ! -f "pyproject.bot.toml" ] && echo "pyproject.bot.toml not found" && exit 1

TAG=$(date +%Y%m%d-%H%M%S)

DOCKER_BUILDKIT=1 docker buildx build \
  --platform linux/arm64 \
  --no-cache \
  -f Dockerfile.bot \
  -t adambehun/bot:${TAG} \
  --push .

mv pcc-deploy.toml pcc-deploy.toml.backup 2>/dev/null || true
cp pcc-deploy.test.toml pcc-deploy.toml
sed -i "s|image = \".*\"|image = \"adambehun/bot:${TAG}\"|" pcc-deploy.toml

pipecat cloud deploy --force

rm pcc-deploy.toml
mv pcc-deploy.toml.backup pcc-deploy.toml 2>/dev/null || true

echo "Deployed: test (bot:${TAG})"
