import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv
from loguru import logger
from pipecat.runner.types import DailyRunnerArguments
from pipeline.runner import ConversationPipeline
from backend.sessions import get_async_session_db
from backend.models import get_async_patient_db
from backend.utils import mask_id, mask_phone
from logging_config import setup_logging

try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from pipecat.utils.tracing.setup import setup_tracing
    TRACING_AVAILABLE = True
except ImportError:
    TRACING_AVAILABLE = False

load_dotenv(override=False)

DEBUG_MODE = os.getenv("DEBUG", "false").lower() in ["true", "1", "yes"]
IS_TRACING_ENABLED = os.getenv("ENABLE_TRACING", "").lower() in ["true", "1", "yes"]

setup_logging(debug=DEBUG_MODE)

if IS_TRACING_ENABLED and TRACING_AVAILABLE:
    try:
        otlp_exporter = OTLPSpanExporter()
        setup_tracing(
            service_name="healthcare-voice-ai",
            exporter=otlp_exporter,
            console_export=os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() in ["true", "1", "yes"],
        )
    except Exception as e:
        logger.error(f"Failed to initialize tracing: {e}")
elif IS_TRACING_ENABLED and not TRACING_AVAILABLE:
    logger.warning("Tracing enabled but OpenTelemetry packages not installed")


async def bot(args: DailyRunnerArguments):
    session_db = get_async_session_db()
    pipeline = None

    try:
        body = args.body
        session_id = body.get("session_id")
        patient_id = body.get("patient_id")
        patient_data = body.get("patient_data")
        client_name = body.get("client_name", "prior_auth")
        organization_id = body.get("organization_id")
        organization_slug = body.get("organization_slug")

        dialin_settings = body.get("dialin_settings")
        dialout_targets = body.get("dialout_targets")
        transfer_config = body.get("transfer_config")

        if dialin_settings:
            call_type = "dial-in"
            phone_number = dialin_settings.get("from", "unknown")
            logger.info(f"DIAL-IN call - call_id={dialin_settings.get('call_id')}, caller={mask_phone(phone_number)}")
        elif dialout_targets and len(dialout_targets) > 0:
            call_type = "dial-out"
            phone_number = dialout_targets[0].get("phoneNumber")
            logger.info(f"DIAL-OUT call - dialing={mask_phone(phone_number)}")
        else:
            raise ValueError("Either dialin_settings or dialout_targets required")

        if not all([session_id, patient_id, patient_data, organization_id, organization_slug]):
            raise ValueError("Missing required: session_id, patient_id, patient_data, organization_id, organization_slug")

        await session_db.update_session(session_id, {
            "status": "running",
            "pid": os.getpid()
        }, organization_id)

        pipeline = ConversationPipeline(
            client_name=client_name,
            session_id=session_id,
            patient_id=patient_id,
            patient_data=patient_data,
            phone_number=phone_number,
            organization_id=organization_id,
            organization_slug=organization_slug,
            call_type=call_type,
            dialin_settings=dialin_settings,
            transfer_config=transfer_config,
            debug_mode=DEBUG_MODE
        )

        room_name = f"call_{session_id}"
        await pipeline.run(args.room_url, args.token, room_name)

        await session_db.update_session(session_id, {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc)
        }, organization_id)

    except Exception as e:
        logger.exception(f"Bot error - session={mask_id(session_id)}")

        try:
            await session_db.update_session(session_id, {
                "status": "failed",
                "completed_at": datetime.now(timezone.utc),
                "error": str(e)
            }, organization_id if 'organization_id' in dir() else None)

            if patient_id and organization_id:
                patient_db = get_async_patient_db()
                await patient_db.update_call_status(patient_id, "Failed", organization_id)
                logger.info(f"Updated patient {mask_id(patient_id)} call_status to Failed")
        except Exception as cleanup_error:
            logger.exception("Failed to update status on error")

        raise

    finally:
        # Don't close Motor client - connection pooling handles cleanup
        # Pipecat Cloud manages container lifecycle
        pass


# Local development mode - runs FastAPI server with /start endpoint
if __name__ == "__main__":
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
                logger.info(f"Local bot start - patient={mask_id(patient_id)}, session={mask_id(session_id)}")

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
                logger.exception("Error starting bot locally")
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
