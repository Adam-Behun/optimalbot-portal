from datetime import datetime
from loguru import logger
from backend.models import get_async_patient_db


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
    """Save collected transcripts to MongoDB when conversation ends"""
    if not pipeline.transcripts:
        logger.warning("No transcripts to save")
        return
    
    try:
        transcript_data = {
            "messages": pipeline.transcripts,
            "message_count": len(pipeline.transcripts),
            "conversation_duration": None
        }
        
        success = await get_async_patient_db().save_call_transcript(
            patient_id=pipeline.patient_id,
            session_id=pipeline.session_id,
            transcript_data=transcript_data
        )
        
        if success:
            logger.info(f"✅ Saved {len(pipeline.transcripts)} transcript messages to MongoDB")
        else:
            logger.error("❌ Failed to save transcript to MongoDB")
            
    except Exception as e:
        logger.error(f"Error saving transcript: {e}")