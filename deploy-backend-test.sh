#!/bin/bash
set -e

APP="optimalbot-test"

echo "Running backend validation..."
python -c "
import asyncio
from backend.config import validate_env_vars, REQUIRED_BACKEND_ENV_VARS
from backend.database import check_connection, close_mongo_client
import os
os.environ['ENV'] = 'test'  # Ensure test mode validation

# Check env vars
valid, missing = validate_env_vars(REQUIRED_BACKEND_ENV_VARS + ['PIPECAT_API_KEY'])
if not valid:
    print(f'Missing env vars: {missing}')
    exit(1)
print('Env vars OK')

# Check MongoDB
async def check():
    ok, err = await check_connection()
    await close_mongo_client()
    return ok, err
ok, err = asyncio.run(check())
if not ok:
    print(f'MongoDB check failed: {err}')
    exit(1)
print('MongoDB OK')
" || { echo "Backend validation failed"; exit 1; }

echo "Syncing secrets from .env..."
# Extract needed vars from .env, override PIPECAT_AGENT_NAME for test
grep -E "^(JWT_SECRET_KEY|MONGO_URI|DAILY_API_KEY|PIPECAT_API_KEY)=" .env > /tmp/fly-secrets.txt
echo "PIPECAT_AGENT_NAME=test" >> /tmp/fly-secrets.txt
echo "ALLOWED_ORIGINS=https://optimalbot-fynr70k3m-adambehun22-4968s-projects.vercel.app" >> /tmp/fly-secrets.txt

fly secrets import -a $APP < /tmp/fly-secrets.txt
rm /tmp/fly-secrets.txt

echo "Deploying..."
fly deploy -c fly.test.toml

echo "Done: https://$APP.fly.dev"
