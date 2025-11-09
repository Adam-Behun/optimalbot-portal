import os
import aiohttp
from datetime import datetime
from loguru import logger
from backend.models import get_async_patient_db


def assemble_transcript(raw_messages: list) -> list:
    if not raw_messages:
        return []

    assembled = []
    current_group = None
    TIME_THRESHOLD = 3.0  # seconds - messages within this window get merged

    for msg in raw_messages:
        try:
            msg_time = datetime.fromisoformat(msg['timestamp'].replace('+00:00', ''))
        except (ValueError, KeyError):
            # If timestamp parsing fails, treat as new message
            msg_time = datetime.now()

        if current_group is None:
            # Start first group
            current_group = msg.copy()
        elif (msg['role'] == current_group['role'] and
              (msg_time - datetime.fromisoformat(current_group['timestamp'].replace('+00:00', ''))).total_seconds() < TIME_THRESHOLD):
            # Same role + within time window -> merge content
            current_group['content'] = current_group['content'].strip() + ' ' + msg['content'].strip()
        else:
            # Different role or time gap -> save current group and start new
            assembled.append(current_group)
            current_group = msg.copy()

    # Don't forget the last group
    if current_group:
        assembled.append(current_group)

    return assembled


def setup_transcript_handler(pipeline):
    """Monitor transcripts for call completion"""

    @pipeline.transcript_processor.event_handler("on_transcript_update")
    async def handle_transcript_update(processor, frame):
        for message in frame.messages:
            transcript_entry = {
                "role": message.role,
                "content": message.content,
                "timestamp": message.timestamp or datetime.now().isoformat(),
                "type": "transcript"
            }
            pipeline.transcripts.append(transcript_entry)
            logger.info(f"[TRANSCRIPT] {message.role}: {message.content}")


async def delete_daily_recording(room_name: str):
    try:
        daily_api_key = os.getenv("DAILY_API_KEY")
        if not daily_api_key:
            logger.warning("DAILY_API_KEY not set, cannot delete recording")
            return False

        # List recordings for the room
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {daily_api_key}",
                "Content-Type": "application/json"
            }

            # Get recordings for this room
            async with session.get(
                f"https://api.daily.co/v1/recordings",
                headers=headers,
                params={"room_name": room_name}
            ) as response:
                if response.status != 200:
                    logger.warning(f"Failed to list recordings: {response.status}")
                    return False

                data = await response.json()
                recordings = data.get("data", [])

                if not recordings:
                    logger.info("No recordings found to delete")
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
                                logger.info(f"✅ Deleted Daily recording: {recording_id}")
                            else:
                                logger.warning(f"Failed to delete recording {recording_id}: {del_response.status}")

                return True

    except Exception as e:
        logger.error(f"Error deleting Daily recordings: {e}")
        return False

async def save_transcript_to_db(pipeline):
    if not pipeline.transcripts:
        logger.warning("No transcripts to save")
        return

    try:
        # Assemble fragmented messages from streaming STT
        assembled_messages = assemble_transcript(pipeline.transcripts)

        transcript_data = {
            "messages": assembled_messages,
            "message_count": len(assembled_messages),
            "raw_message_count": len(pipeline.transcripts),
            "conversation_duration": None
        }

        success = await get_async_patient_db().save_call_transcript(
            patient_id=pipeline.patient_id,
            session_id=pipeline.session_id,
            transcript_data=transcript_data
        )

        if success:
            logger.info(
                f"✅ Saved transcript to MongoDB: "
                f"{len(pipeline.transcripts)} raw messages → {len(assembled_messages)} assembled"
            )

            # Delete Daily recording for HIPAA compliance (minimize PHI retention)
            if hasattr(pipeline, 'transport') and hasattr(pipeline.transport, '_room_name'):
                room_name = pipeline.transport._room_name
                await delete_daily_recording(room_name)
            else:
                logger.warning("Could not determine room name for recording deletion")
        else:
            logger.error("❌ Failed to save transcript to MongoDB")

    except Exception as e:
        logger.error(f"Error saving transcript: {e}")