# E2E Integration Test: IVR → Human Conversation

Tests full flow: Bot calls Twilio (+1-516-585-3321) → navigates IVR menus → Flask server transfers to your phone (+1516-566-7132) → you simulate insurance rep → full conversation.

## Run Test

1. python evals/ivr/twilio_ivr_server.py
2. ngrok http 5001
3. Configure Twilio number webhook: https://YOUR_NGROK_URL/voice
4. python evals/ivr/integration_test.py
