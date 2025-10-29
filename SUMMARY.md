# QUICK REFERENCE

## DEVELOPMENT

Backend changes (app.py, backend/, utils/):
python app.py
http://localhost:8000

Frontend changes (frontend/src/):
cd frontend && npm start
http://localhost:3000

Voice call changes (bot.py, core/, pipeline/, handlers/, services/, clients/):
python bot.py
Note: Bot expects Pipecat Cloud args, use dev setup or test via Pipecat Cloud

## DEPLOY TO PRODUCTION

Backend to Fly.io:
fly deploy
fly logs
curl https://prior-auth-agent-v2.fly.dev/health

Bot to Pipecat Cloud:
docker buildx build --platform linux/arm64 -f Dockerfile.bot -t adambehun/healthcare-bot:latest --push .
pipecatcloud deploy
pipecatcloud agent logs healthcare-voice-ai

Frontend to Vercel:
cd frontend && vercel --prod

## TEST PRODUCTION

Backend health:
curl https://prior-auth-agent-v2.fly.dev/health

Full call test:
1. Login to frontend
2. Create test patient
3. Click "Start Call"
4. Check backend logs: fly logs
5. Check bot logs: pipecatcloud agent logs healthcare-voice-ai

## DEVELOPMENT - STOP/REMOVE

Stop backend:
Ctrl+C (if running python app.py)

Stop frontend:
Ctrl+C (if running npm start)

Remove dev containers (if any):
docker ps
docker stop <container_id>

## PRODUCTION - STOP/REMOVE

Stop backend (Fly.io):
fly scale count 0

Remove backend:
fly apps destroy prior-auth-agent-v2

Stop bot (Pipecat Cloud):
pipecatcloud agent stop healthcare-voice-ai

Remove bot:
pipecatcloud agent delete healthcare-voice-ai

Stop frontend (Vercel):
vercel remove <deployment-url>

## RESTART PRODUCTION

Backend:
fly scale count 1
OR just: fly deploy

Bot:
pipecatcloud agent start healthcare-voice-ai
OR redeploy: pipecatcloud deploy

Frontend:
vercel --prod

## ENVIRONMENT SETUP

Backend .env:
PIPECAT_API_KEY
MONGO_URI
JWT_SECRET_KEY
ALLOWED_ORIGINS

Bot (Pipecat Cloud secrets):
pipecatcloud organizations secrets set healthcare-secrets OPENAI_API_KEY <value>
pipecatcloud organizations secrets set healthcare-secrets DEEPGRAM_API_KEY <value>
pipecatcloud organizations secrets set healthcare-secrets ELEVENLABS_API_KEY <value>
pipecatcloud organizations secrets set healthcare-secrets DAILY_API_KEY <value>
pipecatcloud organizations secrets set healthcare-secrets DAILY_PHONE_NUMBER_ID <value>
pipecatcloud organizations secrets set healthcare-secrets MONGO_URI <value>
