#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    echo "Usage: ./deploy.sh <environment> [component]"
    echo ""
    echo "Environments:"
    echo "  test    Deploy to test environment"
    echo "  prod    Deploy to production (requires confirmation)"
    echo ""
    echo "Components (optional):"
    echo "  backend  Deploy backend only (Fly.io)"
    echo "  bot      Deploy bot only (Pipecat Cloud)"
    echo "  (none)   Deploy both"
    echo ""
    echo "Examples:"
    echo "  ./deploy.sh test          # Deploy both to test"
    echo "  ./deploy.sh test bot      # Deploy bot to test only"
    echo "  ./deploy.sh prod backend  # Deploy backend to prod only"
    exit 1
}

# Generate lockfile if missing
ensure_lockfile() {
    local name=$1
    local pyproject=$2
    local lockfile=$3

    if [ ! -f "$lockfile" ]; then
        echo -e "${YELLOW}Generating $lockfile...${NC}"
        [ -f "pyproject.toml" ] && mv pyproject.toml pyproject.toml.backup
        cp "$pyproject" pyproject.toml
        uv lock --upgrade
        mv uv.lock "$lockfile"
        rm pyproject.toml
        [ -f "pyproject.toml.backup" ] && mv pyproject.toml.backup pyproject.toml
        echo -e "${YELLOW}Consider running ./setup-local.sh to generate all lockfiles${NC}"
    fi
}

# Parse arguments
ENV=$1
COMPONENT=${2:-both}

if [ -z "$ENV" ]; then
    usage
fi

if [ "$ENV" != "test" ] && [ "$ENV" != "prod" ]; then
    echo -e "${RED}Invalid environment: $ENV${NC}"
    usage
fi

if [ "$COMPONENT" != "both" ] && [ "$COMPONENT" != "backend" ] && [ "$COMPONENT" != "bot" ]; then
    echo -e "${RED}Invalid component: $COMPONENT${NC}"
    usage
fi

# Production confirmation
if [ "$ENV" = "prod" ]; then
    read -p "Deploy to PRODUCTION? Type 'yes' to confirm: " confirm
    if [ "$confirm" != "yes" ]; then
        echo "Cancelled"
        exit 0
    fi
fi

# Activate venv for validation
source .venv/bin/activate

# Run validation
echo "Running validation..."
if ! python validate.py --quick; then
    echo -e "${RED}Validation failed. Fix errors before deploying.${NC}"
    exit 1
fi

echo ""
echo "============================================================"
echo "Deploying to ${ENV^^}"
echo "============================================================"

# Deploy backend
deploy_backend() {
    local env=$1
    echo ""
    echo -e "${GREEN}Deploying backend...${NC}"

    # Ensure API lockfile exists
    ensure_lockfile "api" "pyproject.api.toml" "uv.api.lock"

    if [ "$env" = "test" ]; then
        # Sync secrets for test
        grep -E "^(JWT_SECRET_KEY|MONGO_URI|DAILY_API_KEY|PIPECAT_API_KEY|ALLOWED_ORIGINS|SMTP_HOST|SMTP_PORT|SMTP_USERNAME|SMTP_PASSWORD|ALERT_RECIPIENTS)=" .env > /tmp/fly-secrets.txt
        echo "ENV=test" >> /tmp/fly-secrets.txt
        echo "PIPECAT_AGENT_NAME=test" >> /tmp/fly-secrets.txt
        fly secrets import -a optimalbot-test < /tmp/fly-secrets.txt
        rm /tmp/fly-secrets.txt

        fly deploy -c fly.test.toml
        echo -e "${GREEN}Backend deployed: https://optimalbot-test.fly.dev${NC}"
    else
        fly deploy
        echo -e "${GREEN}Backend deployed: https://optimalbot-api.fly.dev${NC}"
    fi
}

# Deploy bot
deploy_bot() {
    local env=$1
    echo ""
    echo -e "${GREEN}Deploying bot...${NC}"

    # Ensure bot lockfile exists
    ensure_lockfile "bot" "pyproject.bot.toml" "uv.bot.lock"

    if [ "$env" = "test" ]; then
        TAG=$(date +%Y%m%d-%H%M%S)

        DOCKER_BUILDKIT=1 docker buildx build \
            --platform linux/arm64 \
            --no-cache \
            -f Dockerfile.bot \
            -t adambehun/bot:${TAG} \
            --push .

        # Use test config - rename to pcc-deploy.toml for CLI
        cp pcc-deploy.toml pcc-deploy.toml.backup
        cp pcc-deploy.test.toml pcc-deploy.toml
        sed -i "s|image = \".*\"|image = \"adambehun/bot:${TAG}\"|" pcc-deploy.toml
        pipecat cloud deploy --force
        mv pcc-deploy.toml.backup pcc-deploy.toml

        echo -e "${GREEN}Bot deployed: test (bot:${TAG})${NC}"
    else
        DOCKER_BUILDKIT=1 docker buildx build \
            --platform linux/arm64 \
            -f Dockerfile.bot \
            -t adambehun/bot:latest \
            --push .

        pipecat cloud deploy --force
        echo -e "${GREEN}Bot deployed: prod (bot:latest)${NC}"
    fi
}

# Execute deployments
if [ "$COMPONENT" = "both" ] || [ "$COMPONENT" = "backend" ]; then
    deploy_backend $ENV
fi

if [ "$COMPONENT" = "both" ] || [ "$COMPONENT" = "bot" ]; then
    deploy_bot $ENV
fi

echo ""
echo -e "${GREEN}Done!${NC}"
