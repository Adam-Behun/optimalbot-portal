"""
Onboarding API endpoints for new client setup.

Handles MP3 upload, transcription, conversation management, and folder structure creation.
"""

import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger

from backend.dependencies import get_onboarding_conversation_db, require_super_admin
from backend.models import AsyncOnboardingConversationRecord
from backend.schemas import ConversationCreate, ConversationUpdate
from backend.services.transcription import transcribe_batch

router = APIRouter()

# Base path for client files (can be overridden via env var)
CLIENTS_BASE_PATH = Path(
    os.getenv("CLIENTS_BASE_PATH", Path(__file__).parent.parent.parent / "clients")
).resolve()

# File upload limits
MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024  # 500MB


def convert_objectid(doc: dict) -> dict:
    """Convert MongoDB ObjectId fields to strings for JSON serialization."""
    if doc is None:
        return doc
    result = dict(doc)
    if "_id" in result:
        result["id"] = str(result.pop("_id"))
    if "organization_id" in result and hasattr(result["organization_id"], "__str__"):
        result["organization_id"] = str(result["organization_id"])
    return result


def sanitize_path_component(name: str) -> str:
    """Sanitize a path component by extracting only safe characters."""
    if not name or not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid name '{name}'. Only alphanumeric, underscores, and hyphens allowed.",
        )
    return "".join(c for c in name.lower() if c.isalnum() or c in "_-")


def validate_conversation_id(conversation_id: str) -> str:
    """Validate MongoDB ObjectId format."""
    if not re.match(r"^[a-f0-9]{24}$", conversation_id):
        raise HTTPException(status_code=400, detail="Invalid conversation_id format")
    return conversation_id


def build_safe_path(*components: str) -> Path:
    """Build a path under CLIENTS_BASE_PATH using only sanitized components."""
    clean_parts = []
    for comp in components:
        clean = "".join(c for c in comp if c.isalnum() or c in "_-.")
        if not clean or clean.startswith("."):
            raise HTTPException(status_code=400, detail="Invalid path component")
        clean_parts.append(clean)

    result = CLIENTS_BASE_PATH.joinpath(*clean_parts).resolve()
    if not str(result).startswith(str(CLIENTS_BASE_PATH)):
        raise HTTPException(status_code=400, detail="Invalid path")
    return result


def _get_files_info(base_path: Path, extension: str) -> dict:
    """Get count and list of files with given extension."""
    if not base_path.exists():
        return {"count": 0, "files": []}
    clean_ext = "".join(c for c in extension if c.isalnum())
    files = sorted([f.name for f in base_path.glob(f"*.{clean_ext}")])
    return {"count": len(files), "files": files}


def get_workflow_path(org: str, workflow: str) -> Path:
    """Get the base path for a workflow, with path traversal protection."""
    safe_org = sanitize_path_component(org)
    safe_workflow = sanitize_path_component(workflow)
    return build_safe_path(safe_org, safe_workflow)


@router.post("/onboarding/upload")
async def upload_recordings(
    org: str = Form(...),
    workflow: str = Form(...),
    files: list[UploadFile] = File(...),
    current_user: dict = Depends(require_super_admin),
):
    """
    Upload MP3 recordings for a new client workflow.

    Creates folder structure:
    - clients/{org}/{workflow}/recordings/
    - clients/{org}/{workflow}/transcripts/
    - clients/{org}/{workflow}/sample_conversations/
    """
    safe_org = sanitize_path_component(org)
    safe_workflow = sanitize_path_component(workflow)

    recordings_path = build_safe_path(safe_org, safe_workflow, "recordings")
    transcripts_path = build_safe_path(safe_org, safe_workflow, "transcripts")
    sample_convos_path = build_safe_path(safe_org, safe_workflow, "sample_conversations")

    recordings_path.mkdir(parents=True, exist_ok=True)
    transcripts_path.mkdir(parents=True, exist_ok=True)
    sample_convos_path.mkdir(parents=True, exist_ok=True)

    uploaded_files = []
    for file in files:
        if not file.filename:
            continue

        filename = os.path.basename(file.filename)
        if not filename or not filename.lower().endswith(".mp3"):
            raise HTTPException(status_code=400, detail=f"Invalid file: '{filename}'")

        content = await file.read(MAX_FILE_SIZE_BYTES + 1)
        if len(content) > MAX_FILE_SIZE_BYTES:
            raise HTTPException(status_code=413, detail=f"File '{filename}' exceeds 500MB")

        file_path = build_safe_path(safe_org, safe_workflow, "recordings", filename)
        with open(file_path, "wb") as f:
            f.write(content)

        uploaded_files.append(filename)
        logger.info(f"Uploaded recording: {filename}")

    return {
        "status": "success",
        "org": org,
        "workflow": workflow,
        "uploaded_files": uploaded_files,
        "path": f"clients/{org}/{workflow}",
    }


@router.get("/onboarding/status/{org}/{workflow}")
async def get_onboarding_status(
    org: str,
    workflow: str,
    current_user: dict = Depends(require_super_admin),
    conv_db: AsyncOnboardingConversationRecord = Depends(get_onboarding_conversation_db),
):
    """
    Get the onboarding status for a workflow.

    Returns counts of files in each stage and conversation stats from MongoDB.
    """
    safe_org = sanitize_path_component(org)
    safe_workflow = sanitize_path_component(workflow)
    workflow_path = build_safe_path(safe_org, safe_workflow)

    if not workflow_path.exists():
        raise HTTPException(status_code=404, detail="Workflow not found")

    recordings_path = build_safe_path(safe_org, safe_workflow, "recordings")
    transcripts_path = build_safe_path(safe_org, safe_workflow, "transcripts")

    recordings = _get_files_info(recordings_path, "mp3")
    transcripts = _get_files_info(transcripts_path, "json")

    conversations = await conv_db.find_by_org_workflow(safe_org, safe_workflow)
    conv_count = len(conversations)
    approved_count = sum(1 for c in conversations if c.get("status") == "approved")

    has_flow_md = build_safe_path(safe_org, safe_workflow, "flow_definition.md").exists()
    has_flow_py = build_safe_path(safe_org, safe_workflow, "flow_definition.py").exists()

    return {
        "org": org,
        "workflow": workflow,
        "path": f"clients/{org}/{workflow}",
        "phases": {
            "recordings": {
                **recordings,
                "complete": recordings["count"] > 0,
            },
            "transcripts": {
                **transcripts,
                "complete": transcripts["count"] >= recordings["count"] and recordings["count"] > 0,
            },
            "sample_conversations": {
                "count": conv_count,
                "approved": approved_count,
                "complete": approved_count > 0,
            },
            "flow_design": {
                "complete": has_flow_md,
            },
            "code_generation": {
                "complete": has_flow_py,
            },
        },
    }


@router.post("/onboarding/transcribe/{org}/{workflow}")
async def transcribe_recordings(
    org: str,
    workflow: str,
    current_user: dict = Depends(require_super_admin),
):
    """
    Trigger Deepgram batch transcription for uploaded recordings.

    Transcribes all MP3 files in the recordings folder and saves
    JSON transcripts to the transcripts folder.
    """
    safe_org = sanitize_path_component(org)
    safe_workflow = sanitize_path_component(workflow)
    recordings_path = build_safe_path(safe_org, safe_workflow, "recordings")
    transcripts_path = build_safe_path(safe_org, safe_workflow, "transcripts")

    if not recordings_path.exists():
        raise HTTPException(status_code=404, detail="Recordings folder not found")

    mp3_files = list(recordings_path.glob("*.mp3"))
    if not mp3_files:
        raise HTTPException(status_code=400, detail="No MP3 files found")

    logger.info(f"Starting transcription of {len(mp3_files)} files")

    # Transcribe all files
    results = await transcribe_batch(mp3_files, transcripts_path)

    # Count successes and failures
    success_count = sum(1 for r in results if r["status"] == "success")
    error_count = sum(1 for r in results if r["status"] == "error")

    return {
        "status": "completed",
        "org": org,
        "workflow": workflow,
        "total_files": len(mp3_files),
        "success_count": success_count,
        "error_count": error_count,
        "results": results,
    }


# =============================================================================
# Conversation Management Endpoints
# =============================================================================


@router.get("/onboarding/conversations/{org}/{workflow}")
async def list_conversations(
    org: str,
    workflow: str,
    current_user: dict = Depends(require_super_admin),
    conv_db: AsyncOnboardingConversationRecord = Depends(get_onboarding_conversation_db),
):
    """
    List all conversations for an organization/workflow.
    """
    safe_org = sanitize_path_component(org)
    safe_workflow = sanitize_path_component(workflow)

    conversations = await conv_db.find_by_org_workflow(safe_org, safe_workflow)
    return {"conversations": [convert_objectid(c) for c in conversations]}


@router.get("/onboarding/conversations/detail/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    current_user: dict = Depends(require_super_admin),
    conv_db: AsyncOnboardingConversationRecord = Depends(get_onboarding_conversation_db),
):
    """
    Get a single conversation by ID.
    """
    validate_conversation_id(conversation_id)

    conversation = await conv_db.find_by_id(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"conversation": convert_objectid(conversation)}


@router.post("/onboarding/conversations")
async def create_conversation(
    data: ConversationCreate,
    current_user: dict = Depends(require_super_admin),
    conv_db: AsyncOnboardingConversationRecord = Depends(get_onboarding_conversation_db),
):
    """
    Create a new conversation document.
    Used by the cleanup agent after processing transcripts.
    """
    safe_org = sanitize_path_component(data.organization_id)
    safe_workflow = sanitize_path_component(data.workflow)

    conversation_data = {
        "organization_id": safe_org,
        "workflow": safe_workflow,
        "source_filename": data.source_filename,
        "assemblyai_id": data.assemblyai_id,
        "roles": data.roles,
        "conversation": [u.model_dump() for u in data.conversation],
        "metadata": data.metadata.model_dump() if data.metadata else {},
    }

    conversation_id = await conv_db.create_conversation(conversation_data)
    if not conversation_id:
        raise HTTPException(status_code=500, detail="Failed to create conversation")

    logger.info(f"Created conversation {conversation_id}")
    return {"status": "success", "conversation_id": conversation_id}


@router.put("/onboarding/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    data: ConversationUpdate,
    current_user: dict = Depends(require_super_admin),
    conv_db: AsyncOnboardingConversationRecord = Depends(get_onboarding_conversation_db),
):
    """
    Update a conversation's roles, utterances, or metadata.
    """
    validate_conversation_id(conversation_id)

    # Check conversation exists
    existing = await conv_db.find_by_id(conversation_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Conversation not found")

    updates = {}
    if data.roles is not None:
        updates["roles"] = data.roles
    if data.conversation is not None:
        updates["conversation"] = [u.model_dump() for u in data.conversation]
    if data.metadata is not None:
        updates["metadata"] = data.metadata.model_dump()

    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    success = await conv_db.update_conversation(conversation_id, updates)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update conversation")

    return {"status": "success"}


@router.post("/onboarding/conversations/{conversation_id}/approve")
async def approve_conversation(
    conversation_id: str,
    current_user: dict = Depends(require_super_admin),
    conv_db: AsyncOnboardingConversationRecord = Depends(get_onboarding_conversation_db),
):
    """
    Mark a conversation as approved.
    """
    validate_conversation_id(conversation_id)

    existing = await conv_db.find_by_id(conversation_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Conversation not found")

    user_id = current_user.get("sub", "unknown")
    success = await conv_db.approve_conversation(conversation_id, user_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to approve conversation")

    logger.info(f"Conversation {conversation_id} approved")
    return {"status": "success"}


@router.delete("/onboarding/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user: dict = Depends(require_super_admin),
    conv_db: AsyncOnboardingConversationRecord = Depends(get_onboarding_conversation_db),
):
    """
    Delete a conversation.
    """
    validate_conversation_id(conversation_id)

    existing = await conv_db.find_by_id(conversation_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Conversation not found")

    success = await conv_db.delete_conversation(conversation_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete conversation")

    logger.info(f"Conversation {conversation_id} deleted")
    return {"status": "success"}
