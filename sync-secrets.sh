#!/bin/bash
set -e

# =============================================================================
# Sync Bot Secrets from .env to Pipecat Cloud
# Usage: ./sync-secrets.sh
# =============================================================================

SECRET_SET="healthcare-secrets"

# Bot-only secrets (not backend stuff like JWT_SECRET_KEY)
BOT_SECRETS=(
    "OPENAI_API_KEY"
    "GROQ_API_KEY"
    "DEEPGRAM_API_KEY"
    "CARTESIA_API_KEY"
    "ELEVENLABS_API_KEY"
    "DAILY_API_KEY"
    "MONGO_URI"
)

echo "Syncing secrets to Pipecat Cloud..."
echo ""

# Set each secret individually to handle special characters properly
for key in "${BOT_SECRETS[@]}"; do
    value=$(grep "^${key}=" .env | cut -d'=' -f2-)
    if [ -n "$value" ]; then
        echo "  Setting $key..."
        # Use KEY=value format, single quotes to preserve special chars
        pipecat cloud secrets set "$SECRET_SET" "${key}=${value}"
    fi
done

echo ""
echo "Done! Secrets synced to '$SECRET_SET'"
echo ""
echo "Verify with: pipecat cloud secrets list $SECRET_SET"
echo ""
echo "Redeploy for changes to take effect:"
echo "  ./deploy.sh test"
