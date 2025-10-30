# QUICK REFERENCE

## DEVELOPMENT

Backend changes (app.py, backend/, utils/):
python app.py
http://localhost:8000

Frontend changes (frontend/src/):
cd frontend && npm start
http://localhost:3000

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

## PRODUCTION - STOP/REMOVE

Stop backend: fly scale count 0
Remove backend: fly apps destroy prior-auth-agent-v2
Stop bot (Pipecat Cloud): pipecatcloud agent stop healthcare-voice-ai
Remove bot:pipecatcloud agent delete healthcare-voice-ai
Stop frontend (Vercel): vercel remove <deployment-url>

## RESTART PRODUCTION
Backend: fly deploy
Bot: pipecatcloud deploy
Frontend: vercel --prod