# Warm Transfer Implementation Guide

This document provides a detailed trace of the warm transfer feature implementation in the patient intake flow. A "warm transfer" means the bot stays on the call to brief the office staff about the patient before connecting them directly.

## Overview

The warm transfer flow allows a patient who requests to speak with a human to be transferred to office staff. The key characteristics are:

1. **Patient is muted and isolated** - they wait in silence while transfer happens
2. **Bot dials office staff** - using Daily.co's dial-out capability
3. **Bot briefs staff** - provides patient context before connecting
4. **Staff and patient connected** - bot exits, leaving them in direct conversation

## Architecture

### Files Involved

| File | Purpose |
|------|---------|
| `clients/demo_clinic_beta/patient_intake/flow_definition.py` | Flow nodes and action handlers |
| `handlers/transport.py` | Daily.co event handlers for participant management |
| `handlers/warm_transfer.py` | Utility functions for participant audio control |

---

## Complete Flow Trace

### Phase 1: Patient Requests Human (Trigger)

**File:** `clients/demo_clinic_beta/patient_intake/flow_definition.py`

#### Step 1.1: `request_staff` Function Available in Nodes

The `request_staff` function is available in multiple nodes where a patient might ask for a human:

**Greeting Node** (lines 200-206):
```python
FlowsFunctionSchema(
    name="request_staff",
    description="Patient is frustrated or explicitly asks to speak with a human/staff member.",
    properties={},
    required=[],
    handler=self._request_staff_handler,
),
```

**Confirmation Node** (lines 380-386):
```python
FlowsFunctionSchema(
    name="request_staff",
    description="Patient is frustrated or explicitly asks to speak with a human/staff member.",
    properties={},
    required=[],
    handler=self._request_staff_handler,
),
```

#### Step 1.2: Handler Initiates Transfer

**File:** `clients/demo_clinic_beta/patient_intake/flow_definition.py` (lines 664-668)

When the LLM calls `request_staff`, the handler transitions to the transfer node:

```python
async def _request_staff_handler(
    self, args: Dict[str, Any], flow_manager: FlowManager
) -> tuple[None, NodeConfig]:
    logger.info("Flow: Patient requested staff - initiating warm transfer")
    return None, self.create_transferring_to_staff_node()
```

**Log output:** `Flow: Patient requested staff - initiating warm transfer`

---

### Phase 2: Transferring to Staff Node

**File:** `clients/demo_clinic_beta/patient_intake/flow_definition.py` (lines 413-429)

```python
def create_transferring_to_staff_node(self) -> NodeConfig:
    return NodeConfig(
        name="transferring_to_staff",
        task_messages=[
            {
                "role": "system",
                "content": "Say: 'I understand. Let me connect you with our office staff. Please hold for just a moment.' Be warm and reassuring.",
            }
        ],
        functions=[],
        pre_actions=[
            {"type": "function", "handler": self._mute_caller},
        ],
        post_actions=[
            {"type": "function", "handler": self._dial_office_staff},
        ],
    )
```

**Execution Order:**
1. `pre_actions` execute FIRST (before LLM speaks)
2. LLM generates and speaks the "please hold" message
3. `post_actions` execute AFTER (when LLM finishes speaking)

#### Step 2.1: Pre-Action - Mute Caller

**File:** `clients/demo_clinic_beta/patient_intake/flow_definition.py` (lines 670-691)

```python
async def _mute_caller(self, action: dict, flow_manager: FlowManager):
    if self.transport:
        participants = self.transport.participants()
        for p in participants.values():
            if not p["info"]["isLocal"]:
                participant_id = p["id"]
                # Store caller ID in state for later use
                flow_manager.state["caller_participant_id"] = participant_id
                # Mute caller AND make them not hear the bot
                await self.transport.update_remote_participants(
                    remote_participants={
                        participant_id: {
                            "permissions": {
                                "canSend": [],  # Cannot speak
                                "canReceive": {"base": False},  # Cannot hear anything
                            }
                        }
                    }
                )
                logger.info(f"Muted and isolated caller: {participant_id}")
                break
```

**What happens:**
- Finds the first non-local participant (the patient/caller)
- Stores their `participant_id` in flow state for later reconnection
- Revokes `canSend` permission (mutes their microphone)
- Revokes `canReceive` permission (they can't hear bot talking to staff)

**Log output:** `Muted and isolated caller: <participant_id>`

**Why this matters:** The patient must be muted BEFORE the bot speaks the "please hold" message. If done in `post_actions`, the patient could interrupt and prevent the transfer.

#### Step 2.2: LLM Speaks to Patient

The bot says: *"I understand. Let me connect you with our office staff. Please hold for just a moment."*

Note: The patient CAN hear this message because isolation happens right before, giving the TTS time to queue. The actual audio isolation takes effect during/after speech.

#### Step 2.3: Post-Action - Dial Office Staff

**File:** `clients/demo_clinic_beta/patient_intake/flow_definition.py` (lines 693-701)

```python
async def _dial_office_staff(self, action: dict, flow_manager: FlowManager):
    office_number = self.warm_transfer_config.get("staff_number")

    if office_number and self.transport:
        flow_manager.state["warm_transfer_in_progress"] = True
        await self.transport.start_dialout({"phoneNumber": office_number})
        logger.info(f"Dialing office staff: {office_number}")
    else:
        logger.error("No staff_number in warm_transfer config")
```

**What happens:**
- Gets staff phone number from `warm_transfer_config` (passed to flow at initialization)
- Sets `warm_transfer_in_progress = True` flag in flow state
- Initiates Daily.co dial-out to the staff number

**Log output:** `Dialing office staff: +1XXXXXXXXXX`

**Configuration:** The `staff_number` comes from `warm_transfer_config` dict passed to `PatientIntakeFlow.__init__()`.

---

### Phase 3: Staff Joins Call

**File:** `handlers/transport.py` (lines 38-74)

When staff answers the phone, Daily.co fires `on_participant_joined`:

```python
@pipeline.transport.event_handler("on_participant_joined")
async def on_participant_joined(transport, participant):
    """Handle when a new participant joins - could be staff during warm transfer."""
    participant_id = participant["id"]
    logger.info(f"✅ Participant joined: {participant_id}")

    # Check if this is staff joining during warm transfer
    if (
        hasattr(pipeline, "flow_manager")
        and pipeline.flow_manager
        and pipeline.flow_manager.state.get("warm_transfer_in_progress")
        and participant_id != getattr(pipeline, "caller_participant_id", None)
    ):
        logger.info("✅ Office staff joined - initiating warm transfer briefing")

        # Store staff participant ID
        pipeline.staff_participant_id = participant_id

        # Capture staff's audio for transcription
        await transport.capture_participant_transcription(participant_id)

        pipeline.transcripts.append({
            "role": "system",
            "content": "Office staff joined - warm transfer in progress",
            "timestamp": datetime.utcnow().isoformat(),
            "type": "transfer"
        })

        await get_async_patient_db().update_call_status(
            pipeline.patient_id, "Warm Transfer", pipeline.organization_id
        )

        # Transition to staff briefing node
        if pipeline.flow:
            briefing_node = pipeline.flow.create_staff_briefing_node()
            await pipeline.flow_manager.set_node_from_config(briefing_node)
            logger.info("✅ Transitioned to staff briefing node")
```

**Detection Logic:**
1. Check `warm_transfer_in_progress` flag is True
2. Check participant is NOT the original caller (by comparing IDs)
3. If both true → this is the staff member joining

**What happens:**
- Stores `staff_participant_id` on pipeline object
- Starts capturing staff's audio for STT
- Updates database status to "Warm Transfer"
- Transitions flow to `staff_briefing_node`

**Log outputs:**
- `✅ Participant joined: <participant_id>`
- `✅ Office staff joined - initiating warm transfer briefing`
- `✅ Transitioned to staff briefing node`

---

### Phase 4: Staff Briefing Node

**File:** `clients/demo_clinic_beta/patient_intake/flow_definition.py` (lines 431-465)

```python
def create_staff_briefing_node(self) -> NodeConfig:
    state = self.flow_manager.state
    first_name = state.get("first_name", "Unknown")
    last_name = state.get("last_name", "")
    reason = state.get("appointment_reason", "Not specified")
    appt_date = state.get("appointment_date", "None")
    appt_time = state.get("appointment_time", "")

    return NodeConfig(
        name="staff_briefing",
        task_messages=[
            {
                "role": "system",
                "content": f"""You're now speaking to office staff. The patient cannot hear you.

Briefly explain:
- Patient: {first_name} {last_name}
- Visit reason: {reason}
- Appointment: {appt_date} at {appt_time}
- Why transfer: Patient requested to speak with a person.

Ask if they're ready to be connected. When they confirm, call connect_to_patient.""",
            }
        ],
        functions=[
            FlowsFunctionSchema(
                name="connect_to_patient",
                description="Staff is ready - connect them to the waiting patient.",
                properties={},
                required=[],
                handler=self._connect_staff_to_patient_handler,
            )
        ],
        respond_immediately=True,
    )
```

**What happens:**
- Bot speaks immediately (`respond_immediately=True`) to greet staff
- Provides collected patient information from flow state
- Only function available is `connect_to_patient`
- Patient cannot hear this conversation (isolated in Phase 2)

**Example bot speech:**
> "Hi, I have a patient on hold. Their name is John Smith. They were calling about a routine checkup. They requested to speak with a person. Are you ready to be connected?"

---

### Phase 5: Connect Staff to Patient

#### Step 5.1: Staff Confirms Ready

When staff says "Yes, connect me" or similar, LLM calls `connect_to_patient`.

**File:** `clients/demo_clinic_beta/patient_intake/flow_definition.py` (lines 703-707)

```python
async def _connect_staff_to_patient_handler(
    self, args: Dict[str, Any], flow_manager: FlowManager
) -> tuple[None, NodeConfig]:
    logger.info("Flow: Staff ready - transitioning to connect")
    return None, self.create_warm_transfer_complete_node()
```

**Log output:** `Flow: Staff ready - transitioning to connect`

#### Step 5.2: Warm Transfer Complete Node

**File:** `clients/demo_clinic_beta/patient_intake/flow_definition.py` (lines 467-481)

```python
def create_warm_transfer_complete_node(self) -> NodeConfig:
    return NodeConfig(
        name="warm_transfer_complete",
        task_messages=[
            {
                "role": "system",
                "content": "Say briefly: 'Connecting you now. Goodbye!'",
            }
        ],
        functions=[],
        post_actions=[
            {"type": "function", "handler": self._connect_and_exit},
            {"type": "end_conversation"},
        ],
    )
```

**Execution Order:**
1. LLM says "Connecting you now. Goodbye!"
2. `post_actions` execute:
   - `_connect_and_exit` reconnects patient and staff
   - `end_conversation` terminates the bot

#### Step 5.3: Connect and Exit Handler

**File:** `clients/demo_clinic_beta/patient_intake/flow_definition.py` (lines 709-744)

```python
async def _connect_and_exit(self, action: dict, flow_manager: FlowManager):
    if self.transport:
        # Get caller ID from state (stored during _mute_caller)
        caller_id = flow_manager.state.get("caller_participant_id")

        # Find staff ID (non-local participant that's not the caller)
        staff_id = None
        participants = self.transport.participants()
        for p in participants.values():
            if p["info"]["isLocal"]:
                continue
            if p["id"] != caller_id:
                staff_id = p["id"]
                break

        if caller_id and staff_id:
            # Connect caller and staff directly - they can hear each other
            await self.transport.update_remote_participants(
                remote_participants={
                    caller_id: {
                        "permissions": {
                            "canSend": ["microphone"],  # Can speak again
                            "canReceive": {"base": True},  # Can hear everyone
                        },
                        "inputsEnabled": {"microphone": True},
                    },
                    staff_id: {
                        "permissions": {
                            "canReceive": {"base": True},  # Can hear everyone
                        },
                    },
                }
            )
            logger.info(f"Connected caller ({caller_id}) and staff ({staff_id})")
        else:
            logger.error(f"Could not connect: caller={caller_id}, staff={staff_id}")
```

**What happens:**
- Retrieves stored `caller_participant_id` from flow state
- Finds staff by looking for non-local, non-caller participant
- Restores caller's permissions:
  - `canSend: ["microphone"]` - unmutes them
  - `canReceive: {"base": True}` - they can hear staff
  - `inputsEnabled: {"microphone": True}` - activates microphone
- Ensures staff can hear caller

**Log output:** `Connected caller (<caller_id>) and staff (<staff_id>)`

---

### Phase 6: Bot Exits

After `_connect_and_exit`, the `end_conversation` action terminates the bot. The patient and staff remain in the Daily room, now able to speak directly to each other.

---

## Error Handling

### Dial-Out Failure

**File:** `handlers/transport.py` (lines 125-164)

If the dial-out to staff fails:

```python
@pipeline.transport.event_handler("on_dialout_error")
async def on_dialout_error(transport, data):
    """Handle dial-out error during warm transfer."""
    if (
        hasattr(pipeline, "flow_manager")
        and pipeline.flow_manager
        and pipeline.flow_manager.state.get("warm_transfer_in_progress")
    ):
        logger.error("❌ Warm transfer dial-out failed - returning to patient")

        pipeline.flow_manager.state["warm_transfer_in_progress"] = False

        # Unmute the original caller
        if hasattr(pipeline, "caller_participant_id"):
            await transport.update_remote_participants(
                remote_participants={
                    pipeline.caller_participant_id: {
                        "permissions": {"canSend": ["microphone"]},
                        "inputsEnabled": {"microphone": True},
                    }
                }
            )
            logger.info("✅ Unmuted caller after failed transfer")

        # Return to confirmation with apology
        if pipeline.flow:
            confirmation_node = pipeline.flow.create_confirmation_node()
            confirmation_node.task_messages[0]["content"] = (
                "Apologize that you couldn't reach office staff and offer to help with anything else. "
                + confirmation_node.task_messages[0]["content"]
            )
            await pipeline.flow_manager.set_node_from_config(confirmation_node)
```

**Recovery actions:**
1. Clears `warm_transfer_in_progress` flag
2. Unmutes the patient
3. Returns to confirmation node with modified prompt to apologize

---

## State Management

### Flow State Variables

| Variable | Set In | Used In | Purpose |
|----------|--------|---------|---------|
| `warm_transfer_in_progress` | `_dial_office_staff` | `on_participant_joined`, `on_dialout_error` | Flag to detect staff joining |
| `caller_participant_id` | `_mute_caller` | `_connect_and_exit` | Track original caller for reconnection |

### Pipeline Object Variables

| Variable | Set In | Used In | Purpose |
|----------|--------|---------|---------|
| `pipeline.caller_participant_id` | `on_first_participant_joined` | `on_participant_joined` | Distinguish caller from staff |
| `pipeline.staff_participant_id` | `on_participant_joined` | General reference | Track staff participant |

---

## Daily.co Permissions Reference

### Permission Structure

```python
{
    "permissions": {
        "canSend": [],  # Empty = muted, ["microphone"] = can speak
        "canReceive": {
            "base": True/False,  # Default receive permission
            "byUserId": {"user_id": True}  # Override for specific users
        }
    },
    "inputsEnabled": {
        "microphone": True/False  # Enable/disable input device
    }
}
```

### Permission States by Phase

| Phase | Patient canSend | Patient canReceive | Staff canReceive |
|-------|-----------------|-------------------|------------------|
| Normal conversation | `["microphone"]` | `{"base": True}` | N/A |
| After mute (Phase 2) | `[]` | `{"base": False}` | N/A |
| Staff briefing (Phase 4) | `[]` | `{"base": False}` | `{"base": True}` |
| Connected (Phase 5) | `["microphone"]` | `{"base": True}` | `{"base": True}` |

---

## Configuration

### Required Configuration

The flow requires `warm_transfer_config` with a `staff_number`:

```python
PatientIntakeFlow(
    patient_data=patient_data,
    flow_manager=flow_manager,
    main_llm=llm,
    transport=transport,
    warm_transfer_config={
        "staff_number": "+15551234567"  # Office staff phone number
    }
)
```

---

## Sequence Diagram

```
Patient          Bot              Daily.co          Staff
   |               |                  |               |
   |--"Talk to human"->              |               |
   |               |                  |               |
   |            [request_staff called]|               |
   |               |                  |               |
   |         [pre_action: mute_caller]|               |
   |               |--update_remote-->|               |
   |               |  (mute+isolate)  |               |
   |               |                  |               |
   |<--"Please hold"                  |               |
   |               |                  |               |
   |         [post_action: dial_office_staff]         |
   |               |--start_dialout-->|               |
   |               |                  |--ring-------->|
   |               |                  |               |
   |   (waiting    |                  |<--answer------|
   |   in silence) |                  |               |
   |               |<-participant_joined              |
   |               |                  |               |
   |            [transition to staff_briefing]        |
   |               |                  |               |
   |               |--"Patient info..."-------------->|
   |               |                  |               |
   |               |<--"Ready to connect"-------------|
   |               |                  |               |
   |            [connect_to_patient called]           |
   |               |                  |               |
   |         [_connect_and_exit]      |               |
   |               |--update_remote-->|               |
   |               |  (reconnect)     |               |
   |               |                  |               |
   |<--"Connecting now. Goodbye!"---->|               |
   |               |                  |               |
   |            [end_conversation]    |               |
   |               X                  |               |
   |                                  |               |
   |<============CONNECTED============|==============>|
```

---

## Utility Functions

**File:** `handlers/warm_transfer.py`

Helper functions for participant management (not currently used in main flow but available):

| Function | Purpose |
|----------|---------|
| `get_participant_by_user_id()` | Find participant ID by their userId |
| `mute_participant()` | Mute a participant by userId |
| `unmute_participant()` | Unmute a participant by userId |
| `isolate_participant_audio()` | Control what a participant can hear |
| `connect_participants()` | Connect two participants for direct audio |

These use `userId` for lookup, which requires participants to join with specific token properties. The main flow uses direct `participant_id` tracking instead since dial-in/dial-out participants don't have userIds set.
