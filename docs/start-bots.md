# Bot Starting Architecture: Pipecat Documentation vs Current Implementation

This document analyzes the official Pipecat patterns for starting bots (both dial-in and dial-out) and compares them against the current MyRobot implementation. It provides a clear roadmap for aligning the codebase with Pipecat best practices.

---

## Table of Contents

1. [Official Pipecat Architecture](#official-pipecat-architecture)
2. [Current MyRobot Implementation](#current-myrobot-implementation)
3. [Key Discrepancies](#key-discrepancies)
4. [Required Changes](#required-changes)
5. [Target Architecture](#target-architecture)
6. [Implementation Checklist](#implementation-checklist)

---

## Official Pipecat Architecture

### Source References

- [Dial-In Example](https://github.com/pipecat-ai/pipecat-examples/tree/main/phone-chatbot/daily-pstn-dial-in)
- [Dial-Out Example](https://github.com/pipecat-ai/pipecat-examples/tree/main/phone-chatbot/daily-pstn-dial-out)
- [Daily PSTN Docs](https://docs.pipecat.ai/guides/telephony/daily-pstn)
- [Pipecat Cloud Dial-In](https://docs.pipecat.ai/deployment/pipecat-cloud/guides/telephony/daily-dial-in)
- [Pipecat Cloud Dial-Out](https://docs.pipecat.ai/deployment/pipecat-cloud/guides/telephony/daily-dial-out)

### Common Pattern: Unified Bot Starting

Both dial-in and dial-out follow the **same pattern** for starting bots:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        UNIFIED BOT STARTING PATTERN                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   1. Server receives request (webhook for dial-in, API for dial-out)       │
│   2. Server creates Daily room using pipecat.runner.daily.configure()      │
│   3. Server builds AgentRequest with room_url, token, and call settings    │
│   4. Server calls start_bot_local() or start_bot_production()              │
│   5. Both functions use identical API structure:                           │
│                                                                             │
│      POST /start (local) or POST /v1/public/{agent}/start (Pipecat Cloud)  │
│      {                                                                      │
│        "createDailyRoom": false,    <-- Room already created               │
│        "body": { ...agent_request }  <-- All data passed here              │
│      }                                                                      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Dial-In: Official Pattern

**File: `server_utils.py` (from pipecat-examples)**

```python
class DailyCallData(BaseModel):
    """Data received from Daily PSTN webhook."""
    from_phone: str
    to_phone: str
    call_id: str
    call_domain: str


class AgentRequest(BaseModel):
    """Request data sent to bot start endpoint."""
    room_url: str
    token: str
    call_id: str
    call_domain: str
    # Custom data fields go here


async def call_data_from_request(request: Request) -> DailyCallData:
    """Parse Daily webhook data."""
    data = await request.json()
    if not all(key in data for key in ["From", "To", "callId", "callDomain"]):
        raise HTTPException(status_code=400, detail="Missing required fields")
    return DailyCallData(
        from_phone=str(data.get("From")),
        to_phone=str(data.get("To")),
        call_id=data.get("callId"),
        call_domain=data.get("callDomain"),
    )


async def create_daily_room(call_data: DailyCallData, session: aiohttp.ClientSession) -> DailyRoomConfig:
    """Create Daily room for dial-in."""
    return await configure(session, sip_caller_phone=call_data.from_phone)


async def start_bot_production(agent_request: AgentRequest, session: aiohttp.ClientSession):
    """Start bot via Pipecat Cloud API."""
    async with session.post(
        f"https://api.pipecat.daily.co/v1/public/{agent_name}/start",
        headers={"Authorization": f"Bearer {pipecat_api_key}", "Content-Type": "application/json"},
        json={"createDailyRoom": False, "body": agent_request.model_dump(exclude_none=True)},
    ) as response:
        if response.status != 200:
            raise HTTPException(status_code=500, detail=await response.text())


async def start_bot_local(agent_request: AgentRequest, session: aiohttp.ClientSession):
    """Start bot via local /start endpoint."""
    async with session.post(
        f"{local_server_url}/start",
        headers={"Content-Type": "application/json"},
        json={"createDailyRoom": False, "body": agent_request.model_dump(exclude_none=True)},
    ) as response:
        if response.status != 200:
            raise HTTPException(status_code=500, detail=await response.text())
```

**File: `server.py` (from pipecat-examples)**

```python
@app.post("/daily-webhook")
async def handle_incoming_daily_webhook(request: Request) -> JSONResponse:
    call_data = await call_data_from_request(request)
    daily_room_config = await create_daily_room(call_data, request.app.state.http_session)

    agent_request = AgentRequest(
        room_url=daily_room_config.room_url,
        token=daily_room_config.token,
        call_id=call_data.call_id,
        call_domain=call_data.call_domain,
    )

    if os.getenv("ENV") == "production":
        await start_bot_production(agent_request, request.app.state.http_session)
    else:
        await start_bot_local(agent_request, request.app.state.http_session)

    return JSONResponse({"status": "success", "room_url": daily_room_config.room_url})
```

**File: `bot.py` (from pipecat-examples) - Key dial-in handling**

```python
async def bot(runner_args: RunnerArguments):
    request = AgentRequest.model_validate(runner_args.body)

    # Create DailyDialinSettings from the call_id and call_domain
    daily_dialin_settings = DailyDialinSettings(
        call_id=request.call_id,
        call_domain=request.call_domain
    )

    transport = DailyTransport(
        request.room_url,
        request.token,
        "Bot Name",
        params=DailyParams(
            api_key=os.getenv("DAILY_API_KEY"),
            dialin_settings=daily_dialin_settings,  # <-- Key difference for dial-in
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
            turn_analyzer=LocalSmartTurnAnalyzerV3(),
        ),
    )
```

### Dial-Out: Official Pattern

**File: `server_utils.py` (from pipecat-examples)**

```python
class DialoutSettings(BaseModel):
    """Settings for outbound call."""
    phone_number: str
    caller_id: str | None = None


class DialoutRequest(BaseModel):
    """Request for dial-out."""
    dialout_settings: DialoutSettings


class AgentRequest(BaseModel):
    """Request data sent to bot."""
    room_url: str
    token: str
    dialout_settings: DialoutSettings


async def create_daily_room(dialout_request: DialoutRequest, session: aiohttp.ClientSession) -> DailyRoomConfig:
    """Create Daily room for dial-out."""
    return await configure(session, sip_caller_phone=dialout_request.dialout_settings.phone_number)
```

**File: `server.py` (from pipecat-examples)**

```python
@app.post("/dialout")
async def handle_dial_out_request(request: Request) -> JSONResponse:
    dialout_request = await dialout_request_from_request(request)
    daily_room_config = await create_daily_room(dialout_request, request.app.state.http_session)

    agent_request = AgentRequest(
        room_url=daily_room_config.room_url,
        token=daily_room_config.token,
        dialout_settings=dialout_request.dialout_settings,
    )

    if os.getenv("ENV") == "production":
        await start_bot_production(agent_request, request.app.state.http_session)
    else:
        await start_bot_local(agent_request, request.app.state.http_session)

    return JSONResponse({"status": "success", "room_url": daily_room_config.room_url})
```

**File: `bot.py` (from pipecat-examples) - Key dial-out handling**

```python
class DialoutManager:
    """Manages dialout with retry logic."""
    def __init__(self, transport: BaseTransport, dialout_settings: DialoutSettings, max_retries: int = 5):
        self._transport = transport
        self._phone_number = dialout_settings.phone_number
        self._max_retries = max_retries
        self._attempt_count = 0
        self._is_successful = False

    async def attempt_dialout(self) -> bool:
        if self._attempt_count >= self._max_retries or self._is_successful:
            return False
        self._attempt_count += 1
        await self._transport.start_dialout({"phoneNumber": self._phone_number})
        return True


async def bot(runner_args: RunnerArguments):
    request = AgentRequest.model_validate(runner_args.body)

    transport = DailyTransport(
        request.room_url,
        request.token,
        "Bot Name",
        params=DailyParams(
            api_key=os.getenv("DAILY_API_KEY"),
            # NO dialin_settings for dial-out
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
            turn_analyzer=LocalSmartTurnAnalyzerV3(),
        ),
    )

    dialout_manager = DialoutManager(transport, request.dialout_settings)

    @transport.event_handler("on_joined")
    async def on_joined(transport, data):
        await dialout_manager.attempt_dialout()

    @transport.event_handler("on_dialout_answered")
    async def on_dialout_answered(transport, data):
        dialout_manager.mark_successful()

    @transport.event_handler("on_dialout_error")
    async def on_dialout_error(transport, data):
        if dialout_manager.should_retry():
            await dialout_manager.attempt_dialout()
        else:
            await task.cancel()
```

---

## Current MyRobot Implementation

### File Structure

```
backend/
├── api/
│   ├── dialin.py      # Dial-in webhook handler
│   └── dialout.py     # Dial-out API (renamed from calls.py)
└── server_utils.py    # Shared bot starting utilities

bot.py                 # Bot entry point
```

### Current Issues

#### Issue 1: Two Different Bot Starting Approaches

**`dialin.py`** uses shared `server_utils.py`:
```python
from backend.server_utils import DialinBotRequest, start_bot_production, start_bot_local

# ... later ...
if ENV == "production":
    await start_bot_production(bot_request, http_session)
else:
    await start_bot_local(bot_request, http_session)
```

**`dialout.py`** uses `pipecatcloud` SDK directly (different pattern):
```python
from pipecatcloud.session import Session, SessionParams

# ... later ...
if ENV == "production":
    pipecat_session = Session(
        agent_name=agent_name,
        api_key=pipecat_api_key,
        params=SessionParams(
            use_daily=True,
            daily_room_properties={...},
            data={...}
        )
    )
    response = await pipecat_session.start()
else:
    await start_bot_local(bot_request, http_session)
```

#### Issue 2: Inconsistent Data Models

**Dial-in** uses `DialinBotRequest`:
```python
class DialinBotRequest(BotRequestBase):
    call_id: str
    call_domain: str
```

**Dial-out** uses `BotRequest`:
```python
class BotRequest(BotRequestBase):
    phone_number: str
```

But the Pipecat pattern uses **separate settings classes**:
- `DailyDialinSettings` (call_id, call_domain)
- `DialoutSettings` (phone_number, caller_id)

#### Issue 3: Missing DialoutManager Pattern

The official dial-out example includes a `DialoutManager` class with:
- Retry logic (up to 5 attempts)
- Event handlers for `on_joined`, `on_dialout_answered`, `on_dialout_error`

Current implementation lacks this retry mechanism.

#### Issue 4: HTTP Session Management

Official pattern uses **app-level shared session**:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_session = aiohttp.ClientSession()
    yield
    await app.state.http_session.close()
```

Current implementation creates **new sessions per request**:
```python
async with aiohttp.ClientSession() as http_session:
    # ... use session ...
```

#### Issue 5: Room Creation Always Done Server-Side

Both Pipecat examples **always** create the room in the server, then pass `createDailyRoom: false` to the bot.

Current `dialout.py` in production mode lets `pipecatcloud.Session` create the room (`use_daily=True`).

---

## Key Discrepancies

| Aspect | Pipecat Official | MyRobot Current |
|--------|-----------------|-----------------|
| **Production bot starting** | Raw HTTP to `/v1/public/{agent}/start` | `pipecatcloud.Session` SDK (dialout) or raw HTTP (dialin) |
| **Room creation** | Always server-side via `configure()` | Server-side (local) or SDK (production dialout) |
| **Data structure** | `AgentRequest` with nested settings | `BotRequest`/`DialinBotRequest` with flat fields |
| **HTTP session** | App-level shared session | Per-request session |
| **Dial-out retry** | `DialoutManager` with 5 retries | No retry mechanism |
| **Settings pattern** | `dialin_settings` / `dialout_settings` objects | Flat fields on bot request |

---

## Required Changes

### 1. Unify Production Bot Starting

**Remove** `pipecatcloud` SDK usage from `dialout.py`. Use the same raw HTTP approach as `dialin.py`:

```python
# Both dial-in and dial-out should use:
await start_bot_production(agent_request, http_session)
```

### 2. Restructure Data Models

**New `server_utils.py` structure:**

```python
class DialinSettings(BaseModel):
    """Settings for incoming call (from Daily webhook)."""
    call_id: str
    call_domain: str
    from_phone: str
    to_phone: str


class DialoutSettings(BaseModel):
    """Settings for outgoing call."""
    phone_number: str
    caller_id: str | None = None


class AgentRequest(BaseModel):
    """Unified request to bot - works for both dial-in and dial-out."""
    room_url: str
    token: str
    session_id: str
    patient_id: str
    patient_data: dict
    client_name: str
    organization_id: str
    organization_slug: str
    # Mutually exclusive - only one will be set
    dialin_settings: DialinSettings | None = None
    dialout_settings: DialoutSettings | None = None
```

### 3. Implement App-Level HTTP Session

**In `backend/main.py`:**

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_session = aiohttp.ClientSession()
    logger.info("Created shared HTTP session")
    yield
    await app.state.http_session.close()
    logger.info("Closed shared HTTP session")

app = FastAPI(title="Healthcare AI Agent", lifespan=lifespan)
```

**In endpoints, use:**
```python
await start_bot_production(agent_request, request.app.state.http_session)
```

### 4. Add DialoutManager to bot.py

```python
class DialoutManager:
    def __init__(self, transport, dialout_settings, max_retries=5):
        self._transport = transport
        self._phone_number = dialout_settings.phone_number
        self._max_retries = max_retries
        self._attempt_count = 0
        self._is_successful = False

    async def attempt_dialout(self) -> bool:
        if self._attempt_count >= self._max_retries or self._is_successful:
            return False
        self._attempt_count += 1
        logger.info(f"Dialout attempt {self._attempt_count}/{self._max_retries} to {self._phone_number}")
        await self._transport.start_dialout({"phoneNumber": self._phone_number})
        return True

    def mark_successful(self):
        self._is_successful = True

    def should_retry(self) -> bool:
        return self._attempt_count < self._max_retries and not self._is_successful
```

### 5. Always Create Room Server-Side

Both endpoints should create the room, then pass `createDailyRoom: false`:

```python
# In dialout.py (production mode)
daily_room_config = await create_daily_room(phone_number, request.app.state.http_session)
agent_request = AgentRequest(
    room_url=daily_room_config.room_url,
    token=daily_room_config.token,
    ...
)
await start_bot_production(agent_request, request.app.state.http_session)
```

---

## Target Architecture

### File Structure (After Refactoring)

```
backend/
├── api/
│   ├── dialin.py           # Dial-in webhook: /dialin-webhook/{client}/{workflow}
│   └── dialout.py          # Dial-out API: /start-call
├── server_utils.py         # Unified bot starting utilities
└── main.py                 # App with shared HTTP session

bot.py                      # Bot with DialoutManager
```

### Unified Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DIAL-IN FLOW                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Daily Webhook ──► dialin.py ──► create_daily_room() ──► AgentRequest      │
│                                  (with dialin_settings)                     │
│                        │                                                    │
│                        ▼                                                    │
│              start_bot_production() or start_bot_local()                    │
│                        │                                                    │
│                        ▼                                                    │
│                    bot.py                                                   │
│                        │                                                    │
│                        ▼                                                    │
│        DailyTransport(dialin_settings=DailyDialinSettings(...))            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                              DIAL-OUT FLOW                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Frontend API ──► dialout.py ──► create_daily_room() ──► AgentRequest      │
│                                  (with dialout_settings)                    │
│                        │                                                    │
│                        ▼                                                    │
│              start_bot_production() or start_bot_local()                    │
│                        │                                                    │
│                        ▼                                                    │
│                    bot.py                                                   │
│                        │                                                    │
│                        ▼                                                    │
│        DailyTransport() + DialoutManager.attempt_dialout()                 │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Checklist

### Phase 1: Refactor `server_utils.py`

- [ ] Create `DialinSettings` model
- [ ] Create `DialoutSettings` model
- [ ] Create unified `AgentRequest` model with optional settings
- [ ] Keep `start_bot_production()` and `start_bot_local()` (already correct pattern)
- [ ] Remove `BotRequest`, `BotRequestBase`, `DialinBotRequest` (replace with `AgentRequest`)

### Phase 2: Add App-Level HTTP Session

- [ ] Add lifespan context manager to `backend/main.py`
- [ ] Update `dialin.py` to use `request.app.state.http_session`
- [ ] Update `dialout.py` to use `request.app.state.http_session`

### Phase 3: Refactor `dialout.py`

- [ ] Remove `pipecatcloud` SDK imports and usage
- [ ] Always create room server-side via `create_daily_room()`
- [ ] Build `AgentRequest` with `dialout_settings`
- [ ] Use `start_bot_production()` for production mode
- [ ] Keep good error handling (can be simplified)

### Phase 4: Refactor `dialin.py`

- [ ] Update to use new `AgentRequest` with `dialin_settings`
- [ ] Use shared HTTP session
- [ ] Minor cleanup to align with dial-out pattern

### Phase 5: Update `bot.py`

- [ ] Add `DialoutManager` class
- [ ] Parse `AgentRequest` from `runner_args.body`
- [ ] Use `dialin_settings` for DailyDialinSettings when present
- [ ] Use `DialoutManager` for dial-out with retry logic
- [ ] Add event handlers: `on_joined`, `on_dialout_answered`, `on_dialout_error`

### Phase 6: Testing

- [ ] Test local dial-out flow
- [ ] Test local dial-in flow
- [ ] Test production dial-out flow
- [ ] Test production dial-in flow
- [ ] Verify retry logic works for dial-out failures

---

## Benefits of This Refactoring

1. **Single Pattern**: Both dial-in and dial-out use identical bot-starting code
2. **Maintainability**: Changes to bot starting only need to happen in one place
3. **Reliability**: DialoutManager provides automatic retry for failed dial-outs
4. **Performance**: Shared HTTP session reduces connection overhead
5. **Clarity**: Clear separation between dial-in settings and dial-out settings
6. **Documentation Alignment**: Matches official Pipecat examples exactly
