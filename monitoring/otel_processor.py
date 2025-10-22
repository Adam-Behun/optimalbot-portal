"""
OpenTelemetry span processor for MongoDB storage.
Captures Pipecat traces and saves to database after calls end.
"""

import logging
from typing import Dict, Any
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanProcessor
from datetime import datetime
import asyncio

from backend.models import get_async_patient_db

logger = logging.getLogger(__name__)


class MongoDBSpanProcessor(SpanProcessor):
    """
    Collects OpenTelemetry spans and saves complete conversations to MongoDB.
    Integrates with Pipecat's built-in tracing system.
    """
    
    def __init__(self, console_debug: bool = False):
        """
        Args:
            console_debug: If True, prints span events to console
        """
        self.conversations: Dict[str, Dict[str, Any]] = {}
        self.console_debug = console_debug
    
    def on_start(self, span: ReadableSpan, parent_context=None) -> None:
        """Called when a span starts"""
        pass
    
    def on_end(self, span: ReadableSpan) -> None:
        """Called when a span ends - collect data here"""
        conversation_id = span.attributes.get("conversation.id")
        if not conversation_id:
            return
        
        # Initialize conversation data structure if first span
        if conversation_id not in self.conversations:
            self.conversations[conversation_id] = self._init_conversation(span)
        
        conv_data = self.conversations[conversation_id]
        
        # Extract patient_id from any span that has it (for MongoDB lookup)
        if not conv_data.get("patient_id"):
            patient_id = span.attributes.get("patient.id")
            if patient_id:
                conv_data["patient_id"] = patient_id
        
        # Route to appropriate handler based on span name
        handlers = {
            "turn": self._handle_turn,
            "stt": self._handle_stt,
            "llm": self._handle_llm,
            "tts": self._handle_tts,
            "conversation": self._handle_conversation_end
        }
        
        handler = handlers.get(span.name)
        if handler:
            handler(span, conv_data)
        
        # Check for state attribute on any span
        state = span.attributes.get("conversation.state")
        if state:
            self._handle_state(span, conv_data, state)
    
    def _init_conversation(self, span: ReadableSpan) -> Dict[str, Any]:
        """
        Initialize conversation data structure.
        
        Note: We only extract patient_id for MongoDB lookup.
        All other metadata (phone, client, etc.) is stored in the 
        transcript_data as context, not used for lookups.
        """
        return {
            "conversation_id": span.attributes.get("conversation.id"),
            "patient_id": None,  # Will be set from first span that has patient.id
            
            # Context metadata (stored in transcript_data, not used for lookups)
            "phone_number": span.attributes.get("phone.number"),
            "client_name": span.attributes.get("client.name"),
            
            "start_time": datetime.fromtimestamp(span.start_time / 1e9).isoformat(),
            "turns": [],
            "transcripts": [],
            "llm_interactions": [],
            "states": [],
            "latency_metrics": {"tts": [], "stt": []},
            "token_usage": {"total_input_tokens": 0, "total_output_tokens": 0}
        }
    
    def _handle_turn(self, span: ReadableSpan, conv_data: Dict) -> None:
        """Handle turn span"""
        turn_data = {
            "turn_number": span.attributes.get("turn.number"),
            "duration_seconds": span.attributes.get("turn.duration_seconds"),
            "was_interrupted": span.attributes.get("turn.was_interrupted"),
            "timestamp": datetime.fromtimestamp(span.start_time / 1e9).isoformat()
        }
        conv_data["turns"].append(turn_data)
        
        if self.console_debug:
            print(f"🔄 Turn {turn_data['turn_number']}: {turn_data['duration_seconds']:.2f}s")
    
    def _handle_stt(self, span: ReadableSpan, conv_data: Dict) -> None:
        """Handle STT (user speech) span"""
        transcript = span.attributes.get("transcript")
        if not transcript:
            return
        
        ttfb = span.attributes.get("metrics.ttfb")
        duration_ms = (span.end_time - span.start_time) / 1e6
        
        user_msg = {
            "type": "user",
            "text": transcript,
            "is_final": span.attributes.get("is_final", True),
            "timestamp": datetime.fromtimestamp(span.start_time / 1e9).isoformat(),
            "ttfb_seconds": ttfb,
            "duration_ms": duration_ms,
            "service": span.attributes.get("gen_ai.system"),
            "model": span.attributes.get("gen_ai.request.model")
        }
        conv_data["transcripts"].append(user_msg)
        
        # Add to STT latency metrics
        if ttfb:
            conv_data["latency_metrics"]["stt"].append({
                "ttfb_seconds": ttfb,
                "duration_ms": duration_ms,
                "timestamp": user_msg["timestamp"],
                "service": user_msg["service"]
            })
        
        if self.console_debug:
            print(f"👤 User: {transcript} (TTFB: {ttfb:.3f}s)" if ttfb else f"👤 User: {transcript}")
    
    def _handle_llm(self, span: ReadableSpan, conv_data: Dict) -> None:
        """Handle LLM span"""
        output = span.attributes.get("output")
        if not output:
            return
        
        ttfb = span.attributes.get("metrics.ttfb")
        duration_ms = (span.end_time - span.start_time) / 1e6
        
        # Add to transcripts (user-visible responses)
        bot_msg = {
            "type": "assistant",
            "text": output,
            "timestamp": datetime.fromtimestamp(span.start_time / 1e9).isoformat(),
            "ttfb_seconds": ttfb,
            "duration_ms": duration_ms,
            "service": span.attributes.get("gen_ai.system"),
            "model": span.attributes.get("gen_ai.request.model")
        }
        conv_data["transcripts"].append(bot_msg)
        
        # Add detailed LLM metadata
        input_tokens = span.attributes.get("gen_ai.usage.input_tokens", 0)
        output_tokens = span.attributes.get("gen_ai.usage.output_tokens", 0)
        
        llm_data = {
            "timestamp": bot_msg["timestamp"],
            "input": span.attributes.get("input"),
            "output": output,
            "system_prompt": span.attributes.get("system"),
            "model": bot_msg["model"],
            "temperature": span.attributes.get("gen_ai.request.temperature"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "ttfb_seconds": ttfb,
            "duration_ms": duration_ms,
            "tools_available": span.attributes.get("tools.names")
        }
        conv_data["llm_interactions"].append(llm_data)
        
        # Aggregate token usage
        conv_data["token_usage"]["total_input_tokens"] += input_tokens
        conv_data["token_usage"]["total_output_tokens"] += output_tokens
        
        if self.console_debug:
            tokens = f"{input_tokens}→{output_tokens}"
            print(f"🤖 Bot: {output[:80]}{'...' if len(output) > 80 else ''}")
            if ttfb:
                print(f"   ↳ Tokens: {tokens}, TTFB: {ttfb:.3f}s, Duration: {duration_ms:.0f}ms")
    
    def _handle_tts(self, span: ReadableSpan, conv_data: Dict) -> None:
        """Handle TTS span"""
        text = span.attributes.get("text")
        if not text:
            return
        
        tts_data = {
            "text_length": len(text),
            "character_count": span.attributes.get("metrics.character_count"),
            "ttfb_seconds": span.attributes.get("metrics.ttfb"),
            "duration_ms": (span.end_time - span.start_time) / 1e6,
            "timestamp": datetime.fromtimestamp(span.start_time / 1e9).isoformat(),
            "voice_id": span.attributes.get("voice_id"),
            "service": span.attributes.get("gen_ai.system")
        }
        conv_data["latency_metrics"]["tts"].append(tts_data)
        
        if self.console_debug and tts_data["ttfb_seconds"]:
            print(f"🔊 TTS: {len(text)} chars, TTFB: {tts_data['ttfb_seconds']:.3f}s")
    
    def _handle_state(self, span: ReadableSpan, conv_data: Dict, state: str) -> None:
        """Handle state transition"""
        conv_data["states"].append({
            "state": state,
            "timestamp": datetime.fromtimestamp(span.start_time / 1e9).isoformat(),
            "duration_ms": (span.end_time - span.start_time) / 1e6
        })
        
        if self.console_debug:
            print(f"🎯 State: {state}")
    
    def _handle_conversation_end(self, span: ReadableSpan, conv_data: Dict) -> None:
        """Handle conversation end - calculate metrics and save to DB"""
        conversation_id = span.attributes.get("conversation.id")
        
        conv_data["end_time"] = datetime.fromtimestamp(span.end_time / 1e9).isoformat()
        conv_data["total_duration_seconds"] = (span.end_time - span.start_time) / 1e9
        
        # Calculate metrics
        conv_data["latency_metrics"]["summary"] = self._calculate_metrics(conv_data)
        
        if self.console_debug:
            self._print_summary(conv_data)
        
        # Save to MongoDB
        asyncio.create_task(self._save_to_mongodb(conversation_id, conv_data))
    
    def _calculate_metrics(self, conv_data: Dict) -> Dict[str, Any]:
        """Calculate summary metrics"""
        turns = conv_data.get("turns", [])
        llm_interactions = conv_data.get("llm_interactions", [])
        tts_metrics = conv_data["latency_metrics"].get("tts", [])
        stt_metrics = conv_data["latency_metrics"].get("stt", [])
        
        summary = {}
        
        # Turn metrics
        if turns:
            durations = [t["duration_seconds"] for t in turns if t.get("duration_seconds")]
            if durations:
                summary["turns"] = {
                    "total": len(turns),
                    "avg_duration_seconds": sum(durations) / len(durations),
                    "min_duration_seconds": min(durations),
                    "max_duration_seconds": max(durations),
                    "interrupted": sum(1 for t in turns if t.get("was_interrupted"))
                }
        
        # LLM metrics
        if llm_interactions:
            ttfbs = [i["ttfb_seconds"] for i in llm_interactions if i.get("ttfb_seconds")]
            durations = [i["duration_ms"] for i in llm_interactions if i.get("duration_ms")]
            if ttfbs:
                summary["llm"] = {
                    "count": len(llm_interactions),
                    "avg_ttfb_seconds": sum(ttfbs) / len(ttfbs),
                    "avg_duration_ms": sum(durations) / len(durations) if durations else 0
                }
        
        # TTS metrics
        if tts_metrics:
            ttfbs = [t["ttfb_seconds"] for t in tts_metrics if t.get("ttfb_seconds")]
            durations = [t["duration_ms"] for t in tts_metrics if t.get("duration_ms")]
            if ttfbs:
                summary["tts"] = {
                    "count": len(tts_metrics),
                    "avg_ttfb_seconds": sum(ttfbs) / len(ttfbs),
                    "avg_duration_ms": sum(durations) / len(durations) if durations else 0
                }
        
        # STT metrics
        if stt_metrics:
            ttfbs = [t["ttfb_seconds"] for t in stt_metrics if t.get("ttfb_seconds")]
            durations = [t["duration_ms"] for t in stt_metrics if t.get("duration_ms")]
            if ttfbs:
                summary["stt"] = {
                    "count": len(stt_metrics),
                    "avg_ttfb_seconds": sum(ttfbs) / len(ttfbs),
                    "avg_duration_ms": sum(durations) / len(durations) if durations else 0
                }
        
        return summary
    
    def _print_summary(self, conv_data: Dict) -> None:
        """Print conversation summary to console"""
        print("\n" + "="*60)
        print(f"📊 CONVERSATION SUMMARY")
        print("="*60)
        print(f"Patient ID: {conv_data.get('patient_id', 'N/A')}")
        print(f"Duration: {conv_data['total_duration_seconds']:.1f}s")
        print(f"Messages: {len(conv_data['transcripts'])}")
        print(f"Turns: {len(conv_data['turns'])}")
        print(f"Tokens: {conv_data['token_usage']['total_input_tokens']}→{conv_data['token_usage']['total_output_tokens']}")
        
        summary = conv_data["latency_metrics"].get("summary", {})
        if "llm" in summary:
            print(f"Avg LLM TTFB: {summary['llm']['avg_ttfb_seconds']:.3f}s")
        if "turns" in summary:
            print(f"Avg Turn Duration: {summary['turns']['avg_duration_seconds']:.2f}s")
        print("="*60 + "\n")
    
    async def _save_to_mongodb(self, conversation_id: str, conv_data: Dict) -> None:
        """
        Save conversation data to MongoDB.
        
        Only patient_id is used for the lookup/update.
        All other data (phone_number, client_name, etc.) is stored 
        in transcript_data as context.
        """
        try:
            patient_id = conv_data.get("patient_id")
            if not patient_id:
                logger.warning(
                    f"❌ No patient_id for conversation {conversation_id}. "
                    f"Ensure 'patient.id' is passed in additional_span_attributes. "
                    f"Conversation will not be saved."
                )
                return
            
            db = get_async_patient_db()
            
            # Only patient_id is used for lookup - everything else is just stored data
            success = await db.save_call_transcript(
                patient_id=patient_id,          # ← Only this matters for lookup
                session_id=conversation_id,
                transcript_data=conv_data       # ← phone_number, client_name stored here as context
            )
            
            if success:
                logger.info(
                    f"✅ Saved conversation to MongoDB: {patient_id} "
                    f"({len(conv_data['transcripts'])} msgs, "
                    f"{len(conv_data['turns'])} turns, "
                    f"{conv_data['total_duration_seconds']:.1f}s)"
                )
            else:
                logger.error(f"❌ Failed to save conversation for patient {patient_id}")
            
            # Clean up from memory
            if conversation_id in self.conversations:
                del self.conversations[conversation_id]
                
        except Exception as e:
            logger.error(f"❌ Error saving conversation to MongoDB: {e}", exc_info=True)
    
    def shutdown(self) -> None:
        """Called on shutdown"""
        pass
    
    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Force flush any pending spans"""
        return True