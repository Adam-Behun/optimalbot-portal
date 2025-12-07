#!/bin/bash
# Regenerate uv.lock with latest package versions
# Run this before deploying to get the newest dependencies

set -e

if [ ! -f "pyproject.bot.toml" ]; then
    echo "❌ pyproject.bot.toml not found"
    exit 1
fi

echo "🔄 Fetching latest versions and regenerating uv.lock..."

# Temporarily create pyproject.toml (uv requires this name)
cp pyproject.bot.toml pyproject.toml

# Generate lockfile with latest versions (--upgrade gets newest)
uv lock --upgrade

# Clean up temporary file
rm pyproject.toml

echo "✅ uv.lock updated with latest versions"
echo ""
echo "📋 Next steps:"
echo "   1. Test locally or deploy to test: ./deploy-test.sh"
echo "   2. If working, deploy to prod: ./deploy-prod.sh"
