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

def save_transcript_to_db(session_id: str, patient_id: str):
    """
    Save transcript to database after call completes.
    Runs in background thread, safe to fire-and-forget.
    """
    import time
    
    # Small delay to ensure all events collected
    time.sleep(0.5)
    
    try:
        # Get full transcript from collector
        collector = get_collector()
        transcript = collector.get_full_transcript(session_id)
        
        # Print to console
        collector.print_full_transcript(session_id)
        collector.print_latency_waterfall(session_id)
        
        # Save to database - create fresh event loop in this thread
        async def save_async():
            patient_db = get_async_patient_db()
            return await patient_db.save_call_transcript(patient_id, session_id, transcript)
        
        # Run async function in new event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            success = loop.run_until_complete(save_async())
            
            if success:
                logger.info(f"✅ Transcript saved to database for patient {patient_id}")
            else:
                logger.error(f"❌ Failed to save transcript for patient {patient_id}")
                
        finally:
            loop.close()
        
    except Exception as e:
        logger.error(f"Failed to save transcript: {e}")