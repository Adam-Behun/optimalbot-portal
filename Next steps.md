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
- Detailed latency monitoring + improve system latency
- Robust pipeline termination
  - Once caller hangs up, terminate
  - Once Closing state, terminate

# 10.18.2025
- 
Implement cost per minute of call tracking
Provide full transcipt after a call, setup for full recording
Fix call status visibility, Patient Details - Back to list button
Start mulitple calls at the same time
Add sign in / log in buttons with mfa
Encrypt data in transit and in storage
Change theme, include navigation menu component

https://ui.shadcn.com/blocks/signup
https://ui.shadcn.com/themes
https://ui.shadcn.com/docs/components/menubar
https://ui.shadcn.com/docs/components/empty
https://ui.shadcn.com/docs/components/sheet
https://ui.shadcn.com/docs/components/pagination#
https://ui.shadcn.com/docs/components/data-table