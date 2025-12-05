# Turn Detection A/B Test: Deepgram Flux vs Smart Turn v3

## Task
Move the Smart Turn v3 patient_intake workflow from `demo_clinic_beta` to `demo_clinic_alpha` so both organizations can run identical conversation logic with different turn detection strategies for A/B comparison.

## Context

**What we're comparing:**

| Organization | Turn Detection | STT Model | Key Difference |
|-------------|----------------|-----------|----------------|
| `demo_clinic_alpha` | Smart Turn v3 + Silero VAD | Deepgram Nova 3 | PyTorch-based, highly tunable |
| `demo_clinic_beta` | Deepgram Flux (built-in) | Deepgram Flux | Single service, simpler pipeline |

**Why this matters:** Smart Turn v3 requires PyTorch (~1.5GB) but offers fine-grained control. Deepgram Flux is lighter and simpler. Running both simultaneously enables direct A/B comparison via Langfuse traces.

## Current State

```
clients/
├── demo_clinic_alpha/
│   ├── prior_auth/
│   └── patient_questions/
│
└── demo_clinic_beta/
    ├── patient_intake/              # Deepgram Flux - KEEP
    ├── patient_intake_smart_turn/   # Smart Turn v3 - MOVE TO ALPHA
    └── patient_questions/
```

## Target State

```
clients/
├── demo_clinic_alpha/
│   ├── prior_auth/
│   ├── patient_intake/      # Smart Turn v3 (moved from beta)
│   └── patient_questions/
│
└── demo_clinic_beta/
    ├── patient_intake/      # Deepgram Flux (unchanged)
    └── patient_questions/
```

---

## Implementation Steps

### Step 1: Move the Workflow Directory

Move the entire `patient_intake_smart_turn` directory from Beta to Alpha, renaming it to `patient_intake`.

**Command:**
```bash
mv clients/demo_clinic_beta/patient_intake_smart_turn clients/demo_clinic_alpha/patient_intake
```

**Verification:** After this step, these files should exist:
- `clients/demo_clinic_alpha/patient_intake/flow_definition.py`
- `clients/demo_clinic_alpha/patient_intake/services.yaml`
- `clients/demo_clinic_alpha/patient_intake/__init__.py`

**Done when:** `ls clients/demo_clinic_alpha/patient_intake/` shows all three files.

---

### Step 2: Update Organization Name References

Edit `clients/demo_clinic_alpha/patient_intake/flow_definition.py` to change "Demo Clinic Beta" to "Demo Clinic Alpha" in two locations:

**Change 1 - Line 22 (warmup_openai function default parameter):**
```python
# BEFORE:
async def warmup_openai(organization_name: str = "Demo Clinic Beta"):

# AFTER:
async def warmup_openai(organization_name: str = "Demo Clinic Alpha"):
```

**Change 2 - Line 122 (PatientIntakeFlow.__init__ default):**
```python
# BEFORE:
self.organization_name = patient_data.get("organization_name", "Demo Clinic Beta")

# AFTER:
self.organization_name = patient_data.get("organization_name", "Demo Clinic Alpha")
```

**Done when:** Running `grep -n "Demo Clinic Beta" clients/demo_clinic_alpha/patient_intake/flow_definition.py` returns no matches.

---

### Step 3: Verify Configuration Files Are Correct

No changes needed here - just verify the configs are different:

**Alpha should have Smart Turn v3** (`clients/demo_clinic_alpha/patient_intake/services.yaml`):
- Contains `turn_detection:` section with `vad.type: silero` and `smart_turn.type: local_v3`
- STT config has `type: deepgram` and `model: nova-3`

**Beta should have Deepgram Flux** (`clients/demo_clinic_beta/patient_intake/services.yaml`):
- No `turn_detection:` section (Flux handles it internally)
- STT config has `model: flux-general-en` with `eager_eot_threshold`, `eot_threshold`, `eot_timeout_ms`

**Done when:** Both config files match their expected turn detection strategy.

---

### Step 4: Enable patient_intake Workflow for Alpha in MongoDB

Update the `organizations` collection to enable the workflow for `demo_clinic_alpha`.

**MongoDB command:**
```javascript
db.organizations.updateOne(
  { slug: "demo_clinic_alpha" },
  { $set: { "workflows.patient_intake": { enabled: true } } }
)
```

**Note:** Run this in your MongoDB shell (local: `mongosh`, production: MongoDB Atlas console or `mongosh` with connection string).

**Done when:** Query `db.organizations.findOne({ slug: "demo_clinic_alpha" })` shows `workflows.patient_intake.enabled: true`.

---

### Step 5: Local Testing

**Start the servers (two terminals):**

Terminal 1 - Backend:
```bash
source venv/bin/activate
ENV=local python app.py
```

Terminal 2 - Bot:
```bash
source venv/bin/activate
python bot.py
```

**Test Alpha (Smart Turn v3):**
```bash
curl -X POST http://localhost:8000/start-call \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"patient_id": "<patient_id>", "client_name": "patient_intake", "organization_slug": "demo_clinic_alpha"}'
```

**Test Beta (Deepgram Flux):**
```bash
curl -X POST http://localhost:8000/start-call \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"patient_id": "<patient_id>", "client_name": "patient_intake", "organization_slug": "demo_clinic_beta"}'
```

**Done when:**
- Alpha logs show: `Smart Turn v3 configured` or similar Silero VAD initialization
- Beta logs show: `Deepgram Flux STT configured` or `model=flux-general-en`
- Both calls connect and the bot responds

---

### Step 6: Deploy to Production

**Build and push Docker image:**
```bash
docker buildx build --platform linux/arm64 -f Dockerfile.bot -t adambehun/healthcare-bot:latest --push .
```

**Deploy to Pipecat Cloud:**
```bash
pipecatcloud deploy
```

**Done when:** `pipecatcloud agent list` shows the updated agent and test calls work in production.

---

## Verification Checklist

After completing all steps, verify:

- [ ] `clients/demo_clinic_alpha/patient_intake/` exists with Smart Turn config
- [ ] `clients/demo_clinic_beta/patient_intake/` exists with Flux config
- [ ] `clients/demo_clinic_beta/patient_intake_smart_turn/` no longer exists (was moved)
- [ ] Alpha's flow_definition.py references "Demo Clinic Alpha" (not Beta)
- [ ] MongoDB has `patient_intake` enabled for `demo_clinic_alpha`
- [ ] Local test: Alpha call initializes Smart Turn v3
- [ ] Local test: Beta call initializes Deepgram Flux
- [ ] Production deployment succeeds
- [ ] Langfuse traces show `organization_slug` tag for filtering

---

## Comparing Results in Langfuse

After running test calls through both organizations:

1. **Filter by organization**: Use `organization_slug` tag to separate traces
2. **Compare latencies**:
   - Time from user stops speaking → bot starts responding
   - STT transcription latency
   - Total turn-around time
3. **Compare accuracy**:
   - Interruption rate (bot speaking over user)
   - Missed turn ends (long pauses before bot responds)
   - False turn ends (bot responds mid-sentence)

---

## Tuning Parameters (Post-Implementation)

### Smart Turn v3 (Alpha)

In `clients/demo_clinic_alpha/patient_intake/services.yaml`:

```yaml
turn_detection:
  vad:
    stop_secs: 0.1          # Very short - Smart Turn makes real decision
    confidence: 0.5         # Lower = catch more speech
  smart_turn:
    stop_secs: 1.5          # Max silence before forcing turn end
    max_duration_secs: 8.0  # Max audio chunk for analysis
```

- Lower `smart_turn.stop_secs` = faster responses, more interruptions
- Higher `smart_turn.stop_secs` = slower responses, fewer interruptions

### Deepgram Flux (Beta)

In `clients/demo_clinic_beta/patient_intake/services.yaml`:

```yaml
services:
  stt:
    eager_eot_threshold: 0.40  # Aggressive early detection
    eot_threshold: 0.50        # Fast confirmation
    eot_timeout_ms: 1200       # Short max wait
```

- Lower `eager_eot_threshold` = faster, more false positives
- Higher `eot_threshold` = more conservative, slower

---

## Rollback Procedures

**If Smart Turn v3 causes issues for Alpha:**

**Option 1: Disable the workflow (immediate)**
```javascript
db.organizations.updateOne(
  { slug: "demo_clinic_alpha" },
  { $set: { "workflows.patient_intake.enabled": false } }
)
```

**Option 2: Switch Alpha to Flux (requires redeploy)**
```bash
cp clients/demo_clinic_beta/patient_intake/services.yaml clients/demo_clinic_alpha/patient_intake/services.yaml
# Then rebuild and deploy
```

---

## Architecture Reference

**Why this works without per-org agents:**

1. Single Docker image contains all organization workflows
2. `FlowLoader` dynamically loads the correct workflow at runtime based on `organization_slug` + `client_name`
3. `ServiceFactory` reads `services.yaml` from the correct path and configures services accordingly
4. No per-org deployment needed - one image, one agent, runtime routing

**Request flow:**
```
Incoming call → Backend → Pipecat Cloud → bot.py
                                            ↓
                                    Receives: organization_slug, client_name
                                            ↓
                                    FlowLoader loads: clients/{org}/{client}/
                                            ↓
                                    ServiceFactory configures from services.yaml
                                            ↓
                                    Pipeline runs with org-specific config
```

---

## Dependencies

Smart Turn v3 requires `pipecat-ai[local-smart-turn-v3]` in `requirements.bot.txt`. This adds PyTorch (~1.5GB to Docker image). Verify this dependency exists before deployment.
