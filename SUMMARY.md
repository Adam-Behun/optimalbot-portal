# Testing & Deployment Guide

## Local Development

### Backend (FastAPI)
source venv/bin/activate
python app.py
http://localhost:8000

### Frontend (React)
cd frontend
npm start
http://localhost:3000

### Deploy Bot to TEST Environment
./deploy-test.sh

### Update backend .env to use test agent
PIPECAT_AGENT_NAME=healthcare-voice-ai-test
python app.py

## Deploy to Production
./deploy-prod.sh

# Update backend .env
PIPECAT_AGENT_NAME=healthcare-voice-ai

# Deploy backend to Fly.io
fly deploy

# Deploy frontend to Vercel
cd frontend && vercel --prod

## Production Deployment

### Backend → Fly.io
fly deploy
fly logs
fly status

curl https://prior-auth-agent-v2.fly.dev/health

./deploy-prod.sh

# Check deployment status
pipecat cloud agent status healthcare-voice-ai

### Frontend → Vercel
cd frontend
vercel --prod

# Check deployment
vercel list

### Complete Removal
```bash
# Backend
fly apps destroy prior-auth-agent-v2

# Bot
pipecat cloud agent delete healthcare-voice-ai

# Frontend
vercel remove <deployment-url>