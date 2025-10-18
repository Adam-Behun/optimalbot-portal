# 10.13.2025
- Add a single patient (add patients in bulk), complete a full call, go through all states in schema.yaml, input prior auth status and reference number into a database
- Website must correctly show call statuses dynamically as they occur - Start Call, Call In Progress, Call Completed
- Newly added patient must show in db, once call completed, Patient row stays in the list with Status = "Call Completed"
- No .js basic popups and confirm messages showed in any step on the frontend (all confirm messages deleted)
- Voice Agent is able to close the call (or realize the human hangs up) and terminate the pipeline after verifying that we have all info needed

# 10.14.2025 - 10.16.2025
- Voicemail detection
- IVR navigation

# 10.17.2025
- Robust pipeline termination
  - Once caller hangs up, terminate
  - Once Closing state, terminate
- Transition is automatic from greeting to verification
- Transition is llm based from verification to closing (llm decides we have all done, status and reference number inserted --> close call)

# 10.18.2025
- Today's topic is observability, monitoring, and evaluations of the main coversation flow

Goals:
1. Imrpove the attached latency monitoring to pinpoint latency issues (setup thresholds, add colors, format numbers)
2. Implement post-call full transcription, including prompts passed, responses generated, user messages into terminal, simply the whole conversation with everything spoken / passed to llm
  - If simple, prepare to push this whole monitoring into frontend (React, Vite, shadcn/ui), so that every call can be reviewed after completion with time stamps, prompts, responses
Details about my implementation are below:
1. I use pipecat, OpenAI, Daily for telephony, Elevenlabs, deepgram, i have multiple states per the conversation with custom prompts being passed into the llm, then there are function calls to update the db. 
2. I have a monitoring setup that I'd like improved. See how it works attached:



# 10.19.2025
- Provide full transcipt after a call

# 10.20.2025
- Encrypt data in transit and in storage

Implement cost per minute of call tracking
Fix call status visibility, Patient Details - Back to list button
Start mulitple calls at the same time
Add sign in / log in buttons with mfa
Change theme, include navigation menu component

https://ui.shadcn.com/blocks/signup
https://ui.shadcn.com/themes
https://ui.shadcn.com/docs/components/menubar
https://ui.shadcn.com/docs/components/empty
https://ui.shadcn.com/docs/components/sheet
https://ui.shadcn.com/docs/components/pagination#
https://ui.shadcn.com/docs/components/data-table
