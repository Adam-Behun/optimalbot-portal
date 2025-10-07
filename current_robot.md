Voice AI Agent for Healthcare Prior Authorization - Improvement Roadmap
Executive Summary
This document provides a comprehensive analysis of the current voice AI agent system and a roadmap for improving its conversation flow to handle real-world insurance verification calls at production scale.

Current State Analysis
System Architecture
Tech Stack:

Backend: FastAPI (Python)
Voice Pipeline: Pipecat framework
Telephony: Daily.co PSTN for outbound calls
LLM: OpenAI GPT-4o-mini
TTS: ElevenLabs (voice: FGY2WhTYpPnrIDTdsKH5, model: eleven_turbo_v2_5)
STT: Deepgram Nova-2
Database: MongoDB (AsyncIOMotorClient)
Frontend: React TypeScript
Deployment: Fly.io (Docker containerized)

Key Components:

app.py - FastAPI server with endpoints:

/start-call - Initiates outbound call via Daily.co REST API
/add-patient - Adds new patient records
/patients - Lists patients with pending authorization
/patients/{id} - Gets patient details
/end-call/{session_id} - Ends call and saves transcript


pipeline.py - HealthcareAIPipeline class:

Creates Daily.co room with dial-out enabled
Manages bot token and room joining
Orchestrates audio pipeline: Input → Resampling → STT → LLM → TTS → Output
Uses Silero VAD for voice activity detection
Manages transcript collection
Integrates FlowManager for conversation state


flow_nodes.py - Defines 4 conversation nodes:

greeting: Initial introduction (waits for insurance rep to speak first)
patient_verification: Provides patient details when asked
authorization_check: Obtains auth status and reference number
closing: Thanks and ends call


transition_handlers.py - Manages flow transitions:

transition_to_verification() - Greeting → Verification
transition_to_authorization() - Verification → Authorization
handle_authorization_update() - Updates DB and transitions to closing


functions.py - LLM function calling:

update_prior_auth_status() - Updates MongoDB with auth status and reference number
Function registry for LLM integration


models.py - Async MongoDB operations:

Patient CRUD operations
Authorization status updates
Call status tracking
Transcript storage



Current Conversation Flow
START
  ↓
[1. GREETING]
- Wait for insurance rep to answer
- Introduce self: "Hi, this is Alexandra from Adam's Medical Practice"
- State purpose: "I'm calling to verify eligibility and benefits"
  ↓
[2. PATIENT VERIFICATION]
- Provide patient name when asked
- Provide DOB (formatted naturally)
- Provide member ID
- Provide CPT code
- Provide NPI
  ↓
[3. AUTHORIZATION CHECK]
- Listen for authorization status (Approved/Denied/Pending)
- Ask for reference number
- Call update_prior_auth_status() function
- Confirm receipt of reference number
  ↓
[4. CLOSING]
- Thank insurance representative
- End call
  ↓
END
Data Schema
Patient Record:
typescript{
  _id: ObjectId,
  patient_name: string,
  date_of_birth: string,
  insurance_member_id?: string,
  insurance_company_name: string,
  facility_name: string,
  cpt_code: string,
  provider_npi: string,
  appointment_time?: string,
  prior_auth_status: "Pending" | "Approved" | "Denied" | "Under Review",
  reference_number?: string,
  call_status: "Not Started" | "In Progress" | "Completed",
  insurance_phone_number?: string,
  call_transcript?: string, // JSON array of messages
  created_at: ISO datetime,
  updated_at: ISO datetime
}
Current Strengths

✅ Clean separation of concerns - Backend, pipeline, flow management, and frontend are modular
✅ Async architecture - Non-blocking I/O with asyncio and Motor
✅ Function calling integration - LLM can update database directly
✅ Transcript persistence - Full conversation history saved to MongoDB
✅ Flow-based conversation - Using pipecat-flows for state management
✅ Production-ready infrastructure - Dockerized, deployable to Fly.io
✅ Proper audio handling - Resampling, VAD, and empty audio filtering
✅ Event-driven telephony - Daily.co event handlers for call lifecycle


Target State
Business Objective
Create a fleet of autonomous voice AI agents capable of:

Calling insurance companies on behalf of healthcare providers
Navigating complex phone systems (IVR, hold music, transfers)
Verifying patient insurance eligibility and benefits
Obtaining prior authorization approvals
Handling edge cases and exceptions gracefully
Operating at production scale with high success rates (>85% call completion)