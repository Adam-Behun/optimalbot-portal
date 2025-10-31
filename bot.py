"""
Pipecat Cloud voice AI bot.
Entry point: async def bot(args) - called by dailyco/pipecat-base image.
"""
import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from pipecat.runner.types import DailyRunnerArguments

from pipeline.runner import ConversationPipeline
from backend.sessions import get_async_session_db

load_dotenv()

# ============================================================================
# OpenTelemetry + Langfuse Initialization
# ============================================================================
# This MUST happen before any pipeline/task creation to capture traces
# Configured via environment variables (see Pipecat Cloud secrets):
# - OTEL_EXPORTER_OTLP_ENDPOINT
# - OTEL_EXPORTER_OTLP_HEADERS

IS_TRACING_ENABLED = bool(os.getenv("ENABLE_TRACING", "").lower() in ["true", "1", "yes"])

if IS_TRACING_ENABLED:
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from pipecat.utils.tracing.setup import setup_tracing

        # Create the exporter (reads config from environment variables)
        otlp_exporter = OTLPSpanExporter()

        # Set up tracing with the exporter
        setup_tracing(
            service_name="healthcare-voice-ai",
            exporter=otlp_exporter,
            console_export=os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() in ["true", "1", "yes"],
        )

        logging.info("✅ OpenTelemetry tracing initialized")
        logging.info(f"   Endpoint: {os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'default')}")

    except ImportError as e:
        logging.warning(f"⚠️ OpenTelemetry dependencies not installed: {e}")
        logging.warning("   Install with: pip install opentelemetry-exporter-otlp-proto-http")
    except Exception as e:
        logging.error(f"❌ Failed to initialize tracing: {e}")
        import traceback
        logging.error(traceback.format_exc())
else:
    logging.info("ℹ️ Tracing disabled (ENABLE_TRACING not set)")

# ============================================================================

logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG for more verbose output
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Silence noisy loggers
logging.getLogger('pymongo').setLevel(logging.WARNING)
logging.getLogger('websockets').setLevel(logging.WARNING)
logging.getLogger('websockets.client').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def bot(args: DailyRunnerArguments):
    """
    Main bot entry point - called by Pipecat Cloud base image.

    Args:
        args.room_url: Daily.co room URL
        args.token: Daily.co meeting token
        args.body: Custom data from start request
        args.session_id: Unique session ID from Pipecat Cloud
    """
    session_db = get_async_session_db()
    pipeline = None

    try:
        logger.info(f"🤖 Bot starting - Session: {args.session_id}")
        logger.debug(f"Room URL: {args.room_url}")
        logger.debug(f"Body keys: {list(args.body.keys())}")

        # Extract required data from body
        patient_id = args.body.get("patient_id")
        patient_data = args.body.get("patient_data")
        phone_number = args.body.get("phone_number")
        client_name = args.body.get("client_name", "prior_auth")

        logger.debug(f"Patient ID: {patient_id}")
        logger.debug(f"Phone number: {phone_number}")
        logger.debug(f"Client name: {client_name}")
        logger.debug(f"Patient name: {patient_data.get('patient_name') if patient_data else 'N/A'}")

        if not all([patient_id, patient_data, phone_number]):
            raise ValueError("Missing required: patient_id, patient_data, phone_number")

        # Update session status
        await session_db.update_session(args.session_id, {
            "status": "running",
            "pid": os.getpid()
        })

        # Create pipeline (fast - no blocking I/O)
        debug_mode = os.getenv("DEBUG", "false").lower() == "true"
        logger.info(f"Debug mode: {debug_mode}")
        logger.info(f"Creating ConversationPipeline for client: {client_name}")

        pipeline = ConversationPipeline(
            client_name=client_name,
            session_id=args.session_id,
            patient_id=patient_id,
            patient_data=patient_data,
            phone_number=phone_number,
            debug_mode=debug_mode
        )

        logger.info("Pipeline created successfully")

        # Run call until completion
        room_name = f"call_{args.session_id}"
        logger.info(f"Starting pipeline.run() for room: {room_name}")
        await pipeline.run(args.room_url, args.token, room_name)

        # Mark completed
        await session_db.update_session(args.session_id, {
            "status": "completed",
            "completed_at": datetime.utcnow()
        })

        logger.info(f"✅ Bot completed - Session: {args.session_id}")

    except Exception as e:
        logger.error(f"❌ Bot error - Session: {args.session_id}, Error: {e}")

        # Mark failed (Pipecat Cloud will restart if needed)
        try:
            await session_db.update_session(args.session_id, {
                "status": "failed",
                "completed_at": datetime.utcnow(),
                "error": str(e)
            })
        except Exception:
            pass  # Don't fail on cleanup

        raise  # Re-raise for Pipecat Cloud to handle

    finally:
        # Cleanup resources on all exit paths (#16)
        if pipeline:
            logger.info("Bot cleanup complete")
        # Close MongoDB connection
        try:
            if hasattr(session_db, 'client'):
                session_db.client.close()
        except Exception:
            pass  # Don't fail on cleanup
