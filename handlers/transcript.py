from datetime import datetime
from loguru import logger
import asyncio
from monitoring import get_collector
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


async def save_transcript_to_db_async(session_id: str, patient_id: str):
    """Save transcript to database - async version (no threading needed)"""
    try:
        await asyncio.sleep(0.5)  # Small delay
        
        collector = get_collector()
        transcript = collector.get_full_transcript(session_id)
        collector.print_full_transcript(session_id)
        collector.print_latency_waterfall(session_id)
        
        patient_db = get_async_patient_db()
        success = await patient_db.save_call_transcript(patient_id, session_id, transcript)
        
        if success:
            logger.info(f"✅ Transcript saved for patient {patient_id}")
        else:
            logger.error(f"❌ Failed to save transcript for patient {patient_id}")
    except Exception as e:
        logger.error(f"Error saving transcript: {e}")