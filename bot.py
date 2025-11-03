import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from pipecat.runner.types import DailyRunnerArguments
from pipeline.runner import ConversationPipeline
from backend.sessions import get_async_session_db
try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from pipecat.utils.tracing.setup import setup_tracing
    TRACING_AVAILABLE = True
except ImportError:
    TRACING_AVAILABLE = False

load_dotenv()

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
        patient_id = args.body.get("patient_id")
        patient_data = args.body.get("patient_data")
        phone_number = args.body.get("phone_number")
        client_name = args.body.get("client_name", "prior_auth")

        if not all([patient_id, patient_data, phone_number]):
            raise ValueError("Missing required: patient_id, patient_data, phone_number")

        await session_db.update_session(args.session_id, {
            "status": "running",
            "pid": os.getpid()
        })

        pipeline = ConversationPipeline(
            client_name=client_name,
            session_id=args.session_id,
            patient_id=patient_id,
            patient_data=patient_data,
            phone_number=phone_number,
            debug_mode=DEBUG_MODE
        )

        room_name = f"call_{args.session_id}"
        await pipeline.run(args.room_url, args.token, room_name)

        await session_db.update_session(args.session_id, {
            "status": "completed",
            "completed_at": datetime.utcnow()
        })

    except Exception as e:
        logger.error(f"Bot error - Session: {args.session_id}, Error: {e}")

        try:
            await session_db.update_session(args.session_id, {
                "status": "failed",
                "completed_at": datetime.utcnow(),
                "error": str(e)
            })
        except Exception:
            pass

        raise

    finally:
        try:
            if hasattr(session_db, 'client'):
                session_db.client.close()
        except Exception:
            pass
