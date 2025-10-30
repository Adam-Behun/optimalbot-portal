# Testing Workflow

Quick guide for testing changes before deploying to production.

## Setup

- **Test Agent:** `healthcare-voice-ai-test` (tag: `test`)
- **Production Agent:** `healthcare-voice-ai` (tag: `latest`)

## Workflow

### 1. Make Changes

```bash
# Edit any bot code
vim handlers/ivr.py
vim clients/prior_auth/services.yaml
```

### 2. Deploy to Test

```bash
./deploy-test.sh
```

Builds ARM64 image, pushes as `adambehun/healthcare-bot:test`, deploys to test agent.

### 3. Point Backend to Test Agent

```bash
# Update .env
PIPECAT_AGENT_NAME=healthcare-voice-ai-test

# Restart backend
python app.py
```

### 4. Test with Real Call

- Open frontend at `http://localhost:3000`
- Start a call - **real phone call** uses your test code
- Monitor logs: `pipecatcloud agent logs healthcare-voice-ai-test --follow`

### 5. Deploy to Production

```bash
./deploy-prod.sh

# Update .env
PIPECAT_AGENT_NAME=healthcare-voice-ai

# Restart backend or update Fly.io:
fly secrets set PIPECAT_AGENT_NAME=healthcare-voice-ai
```

## Quick Reference

| Action | Command |
|--------|---------|
| Deploy to test | `./deploy-test.sh` |
| Deploy to prod | `./deploy-prod.sh` |
| Test logs | `pipecatcloud agent logs healthcare-voice-ai-test` |
| Prod logs | `pipecatcloud agent logs healthcare-voice-ai` |

## Example

```bash
# 1. Edit code
vim handlers/ivr.py

# 2. Deploy & test
./deploy-test.sh
# (Update .env: PIPECAT_AGENT_NAME=healthcare-voice-ai-test)
python app.py
# (Make test call via frontend)

# 3. If good, deploy to prod
./deploy-prod.sh
# (Update .env: PIPECAT_AGENT_NAME=healthcare-voice-ai)
```
