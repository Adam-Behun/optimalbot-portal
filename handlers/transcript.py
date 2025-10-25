from datetime import datetime
from loguru import logger
from backend.models import get_async_patient_db


def assemble_transcript(raw_messages: list) -> list:
    """
    Assemble fragmented transcript messages into coherent conversation.

    Merges consecutive messages from the same role if they occur within
    a time threshold (3 seconds), reducing fragmentation from streaming STT.

    Args:
        raw_messages: List of transcript entries with role, content, timestamp

    Returns:
        List of assembled messages with merged content

    Example:
        Input:  [
            {"role": "user", "content": "Yes. What is the", "timestamp": "..."},
            {"role": "user", "content": "date of birth?", "timestamp": "..."}
        ]
        Output: [
            {"role": "user", "content": "Yes. What is the date of birth?", "timestamp": "..."}
        ]
    """
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
    """Monitor transcripts for state transitions and completion"""
    
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
            
            if message.role == "user":
                await pipeline.state_manager.check_user_transition(message.content)
            elif message.role == "assistant":
                await pipeline.state_manager.check_assistant_transition(message.content)
        
        await pipeline.state_manager.check_completion(pipeline.transcripts)


async def save_transcript_to_db(pipeline):
    """
    Save collected transcripts to MongoDB when conversation ends.

    Assembles fragmented messages before saving to improve readability.
    This runs post-call, so no latency impact on the conversation.
    """
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
        else:
            logger.error("❌ Failed to save transcript to MongoDB")

    except Exception as e:
        logger.error(f"Error saving transcript: {e}")