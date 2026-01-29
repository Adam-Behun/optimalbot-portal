# OptimalBot Portal

## Project Structure

```
portal/
├── backend/          # FastAPI backend (auth, patients, calls API)
├── frontend/         # React SPA (dashboard, patient management)
├── pipeline/         # Voice pipeline (STT → LLM → TTS)
├── clients/          # Flow definitions per client/workflow
├── handlers/         # IVR navigation, call handlers
├── services/         # External service integrations
└── evals/            # Evaluation scenarios and runners
```
