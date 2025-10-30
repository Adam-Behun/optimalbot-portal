# Testing & Deployment Guide

## Local Development

### Backend (FastAPI)
```bash
# Activate venv
source venv/bin/activate

# Run backend server
python app.py

# Test locally
http://localhost:8000
curl http://localhost:8000/health
```

### Frontend (React)
```bash
cd frontend
npm start

# Opens at http://localhost:3000
```

---

## Testing Workflow

### 1. Test Backend Changes Locally
```bash
# Make changes to app.py, backend/, core/, pipeline/, etc.
python app.py

# Test endpoints
curl http://localhost:8000/health
curl -X POST http://localhost:8000/start-call \
  -H "Authorization: Bearer YOUR_JWT" \
  -H "Content-Type: application/json" \
  -d '{"patient_id": "...", "phone_number": "+1234567890"}'
```

### 2. Deploy Bot to TEST Environment
```bash
# This builds and deploys to healthcare-voice-ai-test agent
./deploy-test.sh

# Update backend .env to use test agent
PIPECAT_AGENT_NAME=healthcare-voice-ai-test

# Restart backend
python app.py
```

### 3. Test Full Call Flow
- Use frontend to initiate test call
- Bot runs on `healthcare-voice-ai-test` agent
- Monitor logs: `pipecat cloud agent logs healthcare-voice-ai-test`

### 4. Deploy to Production
Only after testing succeeds:
```bash
# Deploy bot to production
./deploy-prod.sh

# Update backend .env
PIPECAT_AGENT_NAME=healthcare-voice-ai

# Deploy backend to Fly.io
fly deploy

# Deploy frontend to Vercel
cd frontend && vercel --prod
```

---

## Production Deployment

### Backend → Fly.io
```bash
fly deploy
fly logs
fly status

# Verify health
curl https://prior-auth-agent-v2.fly.dev/health
```

### Bot → Pipecat Cloud
```bash
# Production (prompts for confirmation)
./deploy-prod.sh

# Or manually
docker buildx build --platform linux/arm64 -f Dockerfile.bot \
  -t adambehun/healthcare-bot:latest --push .
pipecat cloud deploy -f pcc-deploy.toml

# Check deployment status
pipecat cloud agent status healthcare-voice-ai

# Monitor logs
pipecat cloud agent logs healthcare-voice-ai
```

### Frontend → Vercel
```bash
cd frontend
vercel --prod

# Check deployment
vercel list
```

---

## Environment Variables

### Backend (.env - Fly.io)
```
PIPECAT_API_KEY=...              # Start bot sessions
PIPECAT_AGENT_NAME=healthcare-voice-ai
MONGO_URI=...
JWT_SECRET_KEY=...
ALLOWED_ORIGINS=...
```

### Bot (Pipecat Cloud secret set: healthcare-secrets)
```
OPENAI_API_KEY=...
DEEPGRAM_API_KEY=...
ELEVENLABS_API_KEY=...
DAILY_API_KEY=...
DAILY_PHONE_NUMBER_ID=...
MONGO_URI=...
```

Update secrets:
```bash
pipecat cloud secrets set healthcare-secrets \
  OPENAI_API_KEY=sk-... \
  DEEPGRAM_API_KEY=... \
  MONGO_URI=mongodb://...
```

---

## Monitoring & Debugging

### Backend Logs
```bash
fly logs                    # Tail logs
fly logs --json            # JSON format
fly ssh console            # SSH into container
```

### Bot Logs
```bash
pipecat cloud agent logs healthcare-voice-ai
pipecat cloud agent sessions healthcare-voice-ai
```

### Frontend Logs
```bash
vercel logs <deployment-url>
```

---

## Rollback Production

### Backend
```bash
fly deploy --image <previous-image>
# Or redeploy previous git commit
```

### Bot
```bash
# Redeploy previous image
pipecat cloud deploy healthcare-voice-ai adambehun/healthcare-bot:<previous-tag>
```

### Frontend
```bash
cd frontend
vercel rollback <deployment-url>
```

---

## Stop/Remove Production

### Scale Down (Keep Resources)
```bash
# Backend
fly scale count 0

# Bot (scales to min_agents in pcc-deploy.toml)
pipecat cloud deploy healthcare-voice-ai --min-agents 0

# Frontend (always running on Vercel)
```

### Complete Removal
```bash
# Backend
fly apps destroy prior-auth-agent-v2

# Bot
pipecat cloud agent delete healthcare-voice-ai

# Frontend
vercel remove <deployment-url>
```

---

## Quick Commands Reference

| Task | Command |
|------|---------|
| **Test bot locally** | `python app.py` (backend only) |
| **Deploy to test** | `./deploy-test.sh` |
| **Deploy to prod** | `./deploy-prod.sh` |
| **Backend logs** | `fly logs` |
| **Bot logs** | `pipecat cloud agent logs healthcare-voice-ai` |
| **Check bot status** | `pipecat cloud agent status healthcare-voice-ai` |
| **View active sessions** | `pipecat cloud agent sessions healthcare-voice-ai` |
| **Update secrets** | `pipecat cloud secrets set healthcare-secrets KEY=value` |
