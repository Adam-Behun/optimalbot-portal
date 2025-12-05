import os
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from pipecat.runner.types import DailyRunnerArguments
from pipeline.runner import ConversationPipeline
from backend.sessions import get_async_session_db
from backend.models import get_async_patient_db
try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from pipecat.utils.tracing.setup import setup_tracing
    TRACING_AVAILABLE = True
except ImportError:
    TRACING_AVAILABLE = False

# Load .env for local dev only - won't override Pipecat Cloud secrets
load_dotenv(override=False)

# Determine log level from DEBUG environment variable
DEBUG_MODE = os.getenv("DEBUG", "false").lower() in ["true", "1", "yes"]
LOG_LEVEL = logging.DEBUG if DEBUG_MODE else logging.INFO

IS_TRACING_ENABLED = bool(os.getenv("ENABLE_TRACING", "").lower() in ["true", "1", "yes"])

if IS_TRACING_ENABLED and TRACING_AVAILABLE:
    try:
        otlp_exporter = OTLPSpanExporter()
        setup_tracing(
            service_name="healthcare-voice-ai",
            exporter=otlp_exporter,
            console_export=os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() in ["true", "1", "yes"],
        )
    except Exception as e:
        logging.error(f"Failed to initialize tracing: {e}")
elif IS_TRACING_ENABLED and not TRACING_AVAILABLE:
    logging.warning("Tracing enabled but OpenTelemetry packages not installed")

logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Suppress verbose libraries even in DEBUG mode
logging.getLogger('pymongo').setLevel(logging.WARNING)
logging.getLogger('websockets').setLevel(logging.WARNING)
logging.getLogger('websockets.client').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

if DEBUG_MODE:
    logger.info("🐛 DEBUG mode enabled - verbose logging active")


async def bot(args: DailyRunnerArguments):
    session_db = get_async_session_db()
    pipeline = None

    try:
        session_id = args.body.get("session_id")
        patient_id = args.body.get("patient_id")
        patient_data = args.body.get("patient_data")
        client_name = args.body.get("client_name", "prior_auth")
        organization_id = args.body.get("organization_id")
        organization_slug = args.body.get("organization_slug")

        # Detect dial-in vs dial-out based on presence of call_id/call_domain
        # These fields come ONLY from Daily webhook for incoming calls
        call_id = args.body.get("call_id")
        call_domain = args.body.get("call_domain")
        phone_number = args.body.get("phone_number")

        # Determine call type and dialin_settings
        # CRITICAL: These are mutually exclusive - a call is either dial-in OR dial-out
        if call_id and call_domain:
            # DIAL-IN: Incoming call from Daily webhook
            # - call_id and call_domain are ONLY present for dial-in
            # - phone_number should NOT be present (we don't dial out)
            if phone_number:
                raise ValueError("Dial-in calls must not have phone_number - dial-in numbers cannot make outbound calls")

            call_type = "dial-in"
            dialin_settings = {
                "call_id": call_id,
                "call_domain": call_domain
            }
            # For dial-in, caller's phone is in patient_data (field name: "phone")
            caller_phone = patient_data.get("phone", "unknown")
            logger.info(f"DIAL-IN call detected - Call ID: {call_id}, Caller: {caller_phone}")
        else:
            # DIAL-OUT: Outbound call to phone_number
            # - phone_number is REQUIRED
            # - call_id/call_domain must NOT be present
            if not phone_number:
                raise ValueError("Dial-out calls require phone_number - this number makes outbound calls only")

            call_type = "dial-out"
            dialin_settings = None
            logger.info(f"DIAL-OUT call detected - Dialing: {phone_number}")

        if not all([session_id, patient_id, patient_data, organization_id, organization_slug]):
            raise ValueError("Missing required: session_id, patient_id, patient_data, organization_id, organization_slug")

        await session_db.update_session(session_id, {
            "status": "running",
            "pid": os.getpid()
        }, organization_id)

        # For dial-in, use caller_phone; for dial-out, use phone_number
        display_phone = phone_number if call_type == "dial-out" else patient_data.get("caller_phone", "unknown")

        pipeline = ConversationPipeline(
            client_name=client_name,
            session_id=session_id,
            patient_id=patient_id,
            patient_data=patient_data,
            phone_number=display_phone,
            organization_id=organization_id,
            organization_slug=organization_slug,
            call_type=call_type,
            dialin_settings=dialin_settings,
            debug_mode=DEBUG_MODE
        )

        room_name = f"call_{session_id}"
        await pipeline.run(args.room_url, args.token, room_name)

        await session_db.update_session(session_id, {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc)
        }, organization_id)

    except Exception as e:
        logger.error(f"Bot error - Session: {session_id}, Error: {e}")

        try:
            await session_db.update_session(session_id, {
                "status": "failed",
                "completed_at": datetime.now(timezone.utc),
                "error": str(e)
            }, organization_id if 'organization_id' in dir() else None)

            # Update patient call_status to Failed so frontend stops polling
            if patient_id and organization_id:
                patient_db = get_async_patient_db()
                await patient_db.update_call_status(patient_id, "Failed", organization_id)
                logger.info(f"Updated patient {patient_id} call_status to Failed")
        except Exception as cleanup_error:
            logger.error(f"Failed to update status on error: {cleanup_error}")

        raise

    finally:
        # Don't close Motor client - connection pooling handles cleanup
        # Pipecat Cloud manages container lifecycle
        pass


# Local development mode - runs FastAPI server with /start endpoint
if __name__ == "__main__":
    import sys
    import uvicorn
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    # Check if running in Pipecat runner mode (e.g., uv run bot.py -t daily)
    if any(arg.startswith("-") for arg in sys.argv[1:]):
        from pipecat.runner.run import main
        main()
    else:
        # Default: run local FastAPI server for development
        app = FastAPI()

        class BotStartRequest(BaseModel):
            createDailyRoom: bool = False
            body: dict

        @app.post("/start")
        async def start_bot(request: BotStartRequest):
            """Local bot start endpoint - mimics Pipecat Cloud API"""
            import asyncio

            try:
                patient_id = request.body.get('patient_id')
                session_id = request.body.get('session_id')
                logger.info(f"Received local bot start request for patient {patient_id}, session {session_id}")

                # Create DailyRunnerArguments object
                # We need to create it the same way Pipecat Cloud does
                args = DailyRunnerArguments(
                    room_url=request.body.get("room_url"),
                    token=request.body.get("token"),
                    body=request.body
                )
                # session_id is set as an attribute after construction
                args.session_id = session_id

                # Run bot in background task
                asyncio.create_task(bot(args))

                return {"status": "started", "session_id": session_id}

            except Exception as e:
                logger.error(f"Error starting bot locally: {e}")
                import traceback
                logger.error(traceback.format_exc())
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/health")
        async def health():
            return {"status": "healthy"}

        port = int(os.getenv("BOT_PORT", "7860"))
        logger.info(f"Starting local bot server on port {port}")
        logger.info("=" * 60)
        logger.info("LOCAL DEVELOPMENT MODE")
        logger.info("=" * 60)
        logger.info("Press Ctrl+C to stop the server")

        # Configure uvicorn with proper signal handling
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=port,
            log_level="info",
            timeout_graceful_shutdown=5  # Give 5 seconds for graceful shutdown
        )
        server = uvicorn.Server(config)

        try:
            import asyncio
            asyncio.run(server.serve())
        except KeyboardInterrupt:
            logger.info("Shutting down bot server...")
            sys.exit(0)
