import os
from datetime import datetime, timezone

import aiohttp
from loguru import logger

from backend.models.patient import get_async_patient_db
from backend.sessions import get_async_session_db


def setup_transcript_handler(pipeline):
    """Set up transcript collection using context aggregator events."""
    user_aggregator = pipeline.context_aggregator.user()
    assistant_aggregator = pipeline.context_aggregator.assistant()

    def append_transcript(role: str, message):
        if message.content:
            pipeline.transcripts.append({
                "role": role,
                "content": message.content,
                "timestamp": message.timestamp or datetime.now().isoformat(),
            })

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def handle_user_turn_stopped(aggregator, strategy, message):
        append_transcript("user", message)

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def handle_assistant_turn_stopped(aggregator, message):
        append_transcript("assistant", message)


async def delete_daily_recording(room_name: str):
    try:
        daily_api_key = os.getenv("DAILY_API_KEY")
        if not daily_api_key:
            return False

        # List recordings for the room
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {daily_api_key}",
                "Content-Type": "application/json"
            }

            # Get recordings for this room
            async with session.get(
                "https://api.daily.co/v1/recordings",
                headers=headers,
                params={"room_name": room_name}
            ) as response:
                if response.status != 200:
                    return False

                data = await response.json()
                recordings = data.get("data", [])

                if not recordings:
                    return True

                # Delete each recording
                for recording in recordings:
                    recording_id = recording.get("id")
                    if recording_id:
                        async with session.delete(
                            f"https://api.daily.co/v1/recordings/{recording_id}",
                            headers=headers
                        ) as del_response:
                            if del_response.status in [200, 204]:
                                logger.info("✅ Daily recording deleted (HIPAA compliance)")
                            else:
                                logger.error(f"❌ Failed to delete recording {recording_id}")

                return True

    except Exception as e:
        logger.error(f"❌ Error deleting Daily recordings: {e}")
        return False

async def save_transcript_to_db(pipeline):
    if hasattr(pipeline, 'transcript_saved') and pipeline.transcript_saved:
        logger.info("Transcript already saved, skipping duplicate save")
        return

    if not pipeline.transcripts:
        logger.info("No transcript messages to save")
        return

    try:
        transcript_data = {
            "messages": pipeline.transcripts,
            "message_count": len(pipeline.transcripts),
        }

        # Save to session (works even when patient_id is None for dial-in)
        session_db = get_async_session_db()
        success = await session_db.save_transcript(
            session_id=pipeline.session_id,
            transcript_data=transcript_data,
            organization_id=pipeline.organization_id
        )

        if success:
            pipeline.transcript_saved = True
            logger.info(f"Transcript saved to session ({len(pipeline.transcripts)} messages)")

            # Update patient's last_call reference if patient exists
            if pipeline.patient_id:
                try:
                    patient_db = get_async_patient_db()
                    await patient_db.update_patient(pipeline.patient_id, {
                        "last_call_session_id": pipeline.session_id,
                        "last_call_timestamp": datetime.now(timezone.utc).isoformat()
                    }, pipeline.organization_id)
                    logger.info(f"Patient {pipeline.patient_id} last_call_session_id updated to {pipeline.session_id}")
                except Exception as e:
                    logger.warning(f"Could not update patient last_call reference: {e}")

            # Delete Daily recording for HIPAA compliance
            if hasattr(pipeline, 'transport') and hasattr(pipeline.transport, '_room_name'):
                room_name = pipeline.transport._room_name
                await delete_daily_recording(room_name)
        else:
            logger.error("Failed to save transcript to session")

    except Exception as e:
        logger.error(f"Error saving transcript: {e}")