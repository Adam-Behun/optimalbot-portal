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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
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

        # Extract required data from body
        patient_id = args.body.get("patient_id")
        patient_data = args.body.get("patient_data")
        phone_number = args.body.get("phone_number")
        client_name = args.body.get("client_name", "prior_auth")

        if not all([patient_id, patient_data, phone_number]):
            raise ValueError("Missing required: patient_id, patient_data, phone_number")

        # Update session status
        await session_db.update_session(args.session_id, {
            "status": "running",
            "pid": os.getpid()
        })

        # Create pipeline (fast - no blocking I/O)
        pipeline = ConversationPipeline(
            client_name=client_name,
            session_id=args.session_id,
            patient_id=patient_id,
            patient_data=patient_data,
            phone_number=phone_number,
            debug_mode=os.getenv("DEBUG", "false").lower() == "true"
        )

        # Run call until completion
        room_name = f"call_{args.session_id}"
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
