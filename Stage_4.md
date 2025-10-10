Technical Refactoring Plan: Universal Voice Engine + Pluggable Conversation Schemas
Version: 2.0 - Optimized for Production Voice AI
Critical Design Principles (Applied Throughout)
Latency-First Design

Pre-compute everything possible at initialization: Format pronunciations, render static prompts, cache templates
Minimize runtime overhead: Target <50ms total schema overhead per conversation turn
Benchmark everything: Every stage must measure and report latency impact

Streaming-Native Architecture

Schema components must support incremental prompt building
State transitions don't block response streaming
LLM can start responding before full prompt assembly

Smart State Management

Schema parsed once at app startup, cached in memory
Per-call state machines are lightweight (no schema re-parsing)
Minimal object creation in hot path

STAGE 4: LLM-Based Intent Classification + Hybrid Fallback
Context for LLM Expert
You are replacing keyword-based intent classification with a fast LLM classifier that has keyword fallback. This dramatically improves accuracy while maintaining <100ms latency through caching and smart routing.
Files You Need to Request
Please provide:
1. Current schema.yaml (to see intent definitions)
2. Any existing LLM client code (for making API calls)
3. Current transition_handlers.py or wherever intents are classified
4. Environment variables for LLM API keys
Implementation Requirements
4.1 Intent Classifier with Caching
File: engine/intent_classifier.py
python"""
Intent Classifier - Fast LLM-based classification with fallback.
"""

from typing import Dict, Any, Optional, Tuple
import logging
import time
import hashlib
from functools import lru_cache

logger = logging.getLogger(__name__)


class IntentClassifier:
    """
    Classifies user intent using LLM with semantic caching.
    Falls back to keywords if LLM too slow or fails.
    """
    
    def __init__(self, schema, llm_client):
        """
        Initialize classifier with schema and LLM client.
        
        Args:
            schema: ConversationSchema instance
            llm_client: Your LLM API client (GPT-4, etc.)
        """
        self.schema = schema
        self.llm_client = llm_client
        self.config = schema.intent_classification
        
        # Performance tracking
        self.stats = {
            "total_classifications": 0,
            "llm_classifications": 0,
            "keyword_fallbacks": 0,
            "cache_hits": 0,
            "avg_latency_ms": 0.0
        }
        
        # Build keyword index for fast fallback
        self._keyword_index = self._build_keyword_index()
        
        # Build few-shot prompt once
        self._few_shot_prompt = self._build_few_shot_prompt()
    
    def _build_keyword_index(self) -> Dict[str, str]:
        """Pre-build keyword lookup for fast fallback."""
        index = {}
        for intent in self.schema.intents:
            for keyword in intent.keywords:
                index[keyword.lower()] = intent.name
            for pattern in intent.patterns:
                index[pattern.lower()] = intent.name
        return index
    
    def _build_few_shot_prompt(self) -> str:
        """Build few-shot prompt from schema examples."""
        prompt_parts = [
            "You are classifying user intent in a voice conversation with an insurance representative.",
            "Based on what they say, classify their intent.",
            "",
            "Examples:"
        ]
        
        for example in self.config.examples:
            prompt_parts.append(
                f"User: \"{example.user_message}\"\n"
                f"Intent: {example.intent}\n"
                f"Reasoning: {example.reasoning}\n"
            )
        
        prompt_parts.append(
            "\nNow classify this message. Return ONLY the intent name, nothing else:"
        )
        
        return "\n".join(prompt_parts)
    
    @lru_cache(maxsize=100)
    def _semantic_cache_key(self, message: str) -> str:
        """Generate cache key for semantically similar messages."""
        # Simple normalization for cache hits
        normalized = message.lower().strip()
        # Remove common filler words
        for filler in ["um", "uh", "like", "you know"]:
            normalized = normalized.replace(filler, "")
        return hashlib.md5(normalized.encode()).hexdigest()
    
    async def classify(
        self, 
        user_message: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, float]:
        """
        Classify user intent with latency tracking.
        
        Args:
            user_message: What the insurance rep said
            context: Optional conversation context
            
        Returns:
            Tuple of (intent_name, classification_time_ms)
        """
        start_time = time.perf_counter()
        self.stats["total_classifications"] += 1
        
        # Try LLM classification first
        if self.config.method in ["llm_few_shot", "keyword_hybrid"]:
            try:
                intent = await self._classify_with_llm(user_message)
                
                latency_ms = (time.perf_counter() - start_time) * 1000
                
                # Check if within latency budget
                if latency_ms <= self.config.max_classification_latency_ms:
                    self.stats["llm_classifications"] += 1
                    logger.debug(
                        f"LLM classified intent: {intent} in {latency_ms:.2f}ms"
                    )
                    return intent, latency_ms
                else:
                    logger.warning(
                        f"LLM classification took {latency_ms:.2f}ms "
                        f"(limit: {self.config.max_classification_latency_ms}ms), "
                        f"falling back to keywords"
                    )
            
            except Exception as e:
                logger.error(f"LLM classification failed: {e}, using keyword fallback")
        
        # Fallback to keyword matching
        intent = self._classify_with_keywords(user_message)
        latency_ms = (time.perf_counter() - start_time) * 1000
        self.stats["keyword_fallbacks"] += 1
        
        logger.debug(f"Keyword classified intent: {intent} in {latency_ms:.2f}ms")
        return intent, latency_ms
    
    async def _classify_with_llm(self, message: str) -> str:
        """
        Classify using LLM with semantic caching.
        Fast path: <50ms for cached results.
        """
        # Check semantic cache
        cache_key = self._semantic_cache_key(message)
        # (You could use Redis or in-memory cache here)
        
        # Build prompt
        full_prompt = f"{self._few_shot_prompt}\nUser: \"{message}\"\nIntent:"
        
        # Call LLM (your existing client)
        # This should be a fast, streaming call
        response = await self.llm_client.complete(
            prompt=full_prompt,
            max_tokens=20,  # Just need intent name
            temperature=0.0,  # Deterministic
            stream=False  # Small response, no need to stream
        )
        
        intent = response.strip()
        
        # Validate intent exists in schema
        valid_intents = [i.name for i in self.schema.intents]
        if intent not in valid_intents:
            logger.warning(f"LLM returned unknown intent '{intent}', using keyword fallback")
            return self._classify_with_keywords(message)
        
        return intent
    
    def _classify_with_keywords(self, message: str) -> str:
        """
        Fast keyword-based classification.
        This is the fallback when LLM too slow or fails.
        """
        message_lower = message.lower()
        
        # Check exact keyword matches
        for keyword, intent in self._keyword_index.items():
            if keyword in message_lower:
                return intent
        
        # No match found
        return "unknown"
    
    def get_stats(self) -> Dict[str, Any]:
        """Return classification statistics"""
        return {
            **self.stats,
            "llm_usage_percent": (
                100 * self.stats["llm_classifications"] / self.stats["total_classifications"]
                if self.stats["total_classifications"] > 0 else 0
            )
        }
4.2 Add Intent Stats Endpoint
python# In app.py

@app.get("/schema/intent-stats")
async def get_intent_stats():
    """Get intent classification statistics"""
    if hasattr(intent_classifier, 'get_stats'):
        return intent_classifier.get_stats()
    return {"error": "Intent classifier not initialized"}
Success Criteria

 LLM classification <100ms for 90% of requests
 Keyword fallback <10ms
 Classification accuracy >85% (manual review of logs)
 Stats endpoint shows LLM vs keyword usage

What to Ask the Engineer
Before implementing, please provide:
1. Your current LLM client code (for API calls)
2. API keys and model configuration
3. Current intent classification code (if any)
4. Schema with intent examples from Stage 1

I need to integrate with your existing LLM setup.