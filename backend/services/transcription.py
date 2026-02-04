"""
AssemblyAI batch transcription service with speaker diarization.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import assemblyai as aai
from loguru import logger

ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY", "")

# Configure AssemblyAI (must set base_url per docs)
if ASSEMBLYAI_API_KEY:
    aai.settings.base_url = "https://api.assemblyai.com"
    aai.settings.api_key = ASSEMBLYAI_API_KEY


def transcribe_file_sync(file_path: Path, output_path: Path) -> dict[str, Any]:
    """
    Transcribe a single audio file using AssemblyAI with speaker diarization.

    This is a synchronous function that will be run in a thread pool.
    """
    if not ASSEMBLYAI_API_KEY:
        raise ValueError("ASSEMBLYAI_API_KEY environment variable is not set")

    # Domain-specific prompt for insurance eligibility verification calls
    prompt = """Mandatory: Insurance eligibility verification call between healthcare practice staff and insurance payer representative.
Required: Preserve all speaker disfluencies including verbal hesitations, restarts, and self-corrections (um, uh, I—I mean).
Non-negotiable: Entity accuracy for insurance terms (copay, coinsurance, deductible, prior authorization, CPT code, member ID, NPI, group number, effective date, out-of-pocket maximum).
Required: Mark speaker turns clearly and tag speakers by role when identifiable (Provider Agent:, Insurance Agent:, IVR:).
Required: Use digits for all numbers including dollar amounts, percentages, dates, and ID numbers."""

    config = aai.TranscriptionConfig(
        # Best models for accuracy
        speech_models=["universal-3-pro", "universal-2"],
        language_detection=True,
        # Domain-specific prompt for improved accuracy
        prompt=prompt,
        # Speaker diarization with flexible range
        # (insurance calls may have IVR, transfers, supervisors)
        speaker_labels=True,
        speaker_options=aai.SpeakerOptions(
            min_speakers_expected=2,
            max_speakers_expected=4,
        ),
        punctuate=True,
        format_text=True,
    )

    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(str(file_path), config=config)

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"Transcription failed: {transcript.error}")

    # Build result dict with utterances only (no word-level data to keep files small)
    result = {
        "id": transcript.id,
        "status": transcript.status.value,
        "text": transcript.text,
        "utterances": [
            {
                "speaker": u.speaker,
                "text": u.text,
                "confidence": u.confidence,
            }
            for u in (transcript.utterances or [])
        ],
        "confidence": transcript.confidence,
        "audio_duration": transcript.audio_duration,
    }

    # Save transcript to output file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    return result


async def transcribe_file(file_path: Path, output_path: Path) -> dict[str, Any]:
    """
    Transcribe a single audio file (async wrapper).
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, transcribe_file_sync, file_path, output_path
    )


async def transcribe_batch(
    files: list[Path], output_dir: Path, max_concurrent: int = 10
) -> list[dict[str, Any]]:
    """
    Transcribe multiple audio files concurrently.

    Args:
        files: List of paths to audio files
        output_dir: Directory where JSON transcripts should be saved
        max_concurrent: Maximum concurrent transcriptions (default 10, AssemblyAI allows 32)

    Returns:
        List of transcription results with status
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def transcribe_with_limit(file_path: Path) -> dict[str, Any]:
        async with semaphore:
            output_path = output_dir / f"{file_path.stem}.json"
            try:
                logger.info(f"Starting transcription: {file_path.name}")
                result = await transcribe_file(file_path, output_path)
                logger.info(f"Completed transcription: {file_path.name}")
                return {
                    "file": file_path.name,
                    "status": "success",
                    "output": str(output_path),
                    "duration_seconds": result.get("audio_duration"),
                }
            except Exception as e:
                logger.error(f"Failed to transcribe {file_path.name}: {e}")
                return {
                    "file": file_path.name,
                    "status": "error",
                    "error": str(e),
                }

    tasks = [transcribe_with_limit(f) for f in files]
    results = await asyncio.gather(*tasks)
    return list(results)
