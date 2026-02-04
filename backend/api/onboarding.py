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


def validate_name(name: str) -> str:
    """Validate and sanitize org/workflow name to prevent path traversal."""
    # Only allow alphanumeric, underscores, and hyphens
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid name '{name}'. Only alphanumeric, underscores, and hyphens allowed.",
        )
    return name.lower()


def validate_conversation_id(conversation_id: str) -> str:
    """Validate MongoDB ObjectId format."""
    if not re.match(r"^[a-f0-9]{24}$", conversation_id):
        raise HTTPException(status_code=400, detail="Invalid conversation_id format")
    return conversation_id


def safe_path(path: Path) -> Path:
    """
    Validate a path is under CLIENTS_BASE_PATH and return resolved path.

    This function serves as a security gate for all file operations.
    """
    resolved = path.resolve()
    try:
        resolved.relative_to(CLIENTS_BASE_PATH)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid path: access denied",
        )
    return resolved


def _get_files_info(path: Path, extension: str) -> dict:
    """Get count and list of files with given extension."""
    # Security: verify path is under CLIENTS_BASE_PATH
    resolved = path.resolve()
    try:
        resolved.relative_to(CLIENTS_BASE_PATH)
    except ValueError:
        return {"count": 0, "files": []}

    if not resolved.exists():
        return {"count": 0, "files": []}
    files = sorted([f.name for f in resolved.glob(f"*.{extension}")])
    return {"count": len(files), "files": files}


def get_workflow_path(org: str, workflow: str) -> Path:
    """Get the base path for a workflow, with path traversal protection."""
    safe_org = validate_name(org)
    safe_workflow = validate_name(workflow)

    for name in [safe_org, safe_workflow]:  # defense-in-depth check
        if ".." in name or "/" in name or "\\" in name:
            raise HTTPException(status_code=400, detail="Invalid path")

    return (CLIENTS_BASE_PATH / safe_org / safe_workflow).resolve()


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
    workflow_path = get_workflow_path(org, workflow)

    # Create directory structure with path validation
    recordings_path = safe_path(workflow_path / "recordings")
    transcripts_path = safe_path(workflow_path / "transcripts")
    sample_convos_path = safe_path(workflow_path / "sample_conversations")

    recordings_path.mkdir(parents=True, exist_ok=True)
    transcripts_path.mkdir(parents=True, exist_ok=True)
    sample_convos_path.mkdir(parents=True, exist_ok=True)

    uploaded_files = []
    for file in files:
        if not file.filename:
            continue

        # Sanitize filename to prevent path traversal
        safe_filename = os.path.basename(file.filename)
        if not safe_filename:
            continue

        # Validate file extension
        if not safe_filename.lower().endswith(".mp3"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type for '{safe_filename}'. Only MP3 files allowed.",
            )

        # Read file with size limit check
        content = await file.read(MAX_FILE_SIZE_BYTES + 1)
        if len(content) > MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File '{safe_filename}' exceeds 500MB limit.",
            )

        # Save file with path validation
        file_path = safe_path(recordings_path / safe_filename)
        with open(file_path, "wb") as f:
            f.write(content)

        uploaded_files.append(safe_filename)
        logger.info(f"Uploaded recording: {safe_filename}")

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
    workflow_path = get_workflow_path(org, workflow)
    validated_path = safe_path(workflow_path)

    if not validated_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Workflow not found",
        )

    recordings_path = safe_path(workflow_path / "recordings")
    transcripts_path = safe_path(workflow_path / "transcripts")

    recordings = _get_files_info(recordings_path, "mp3")
    transcripts = _get_files_info(transcripts_path, "json")

    # Get conversation counts from MongoDB
    org_validated = validate_name(org)
    workflow_validated = validate_name(workflow)
    conversations = await conv_db.find_by_org_workflow(org_validated, workflow_validated)
    conv_count = len(conversations)
    approved_count = sum(1 for c in conversations if c.get("status") == "approved")

    # Check for flow definition files
    has_flow_md = safe_path(workflow_path / "flow_definition.md").exists()
    has_flow_py = safe_path(workflow_path / "flow_definition.py").exists()

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
    workflow_path = get_workflow_path(org, workflow)
    recordings_path = safe_path(workflow_path / "recordings")
    transcripts_path = safe_path(workflow_path / "transcripts")

    if not recordings_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Recordings folder not found",
        )

    # Get all MP3 files
    mp3_files = list(recordings_path.glob("*.mp3"))
    if not mp3_files:
        raise HTTPException(
            status_code=400,
            detail="No MP3 files found in recordings folder",
        )

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
    org = validate_name(org)
    workflow = validate_name(workflow)

    conversations = await conv_db.find_by_org_workflow(org, workflow)
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
    # Validate org/workflow names
    org = validate_name(data.organization_id)
    workflow = validate_name(data.workflow)

    conversation_data = {
        "organization_id": org,
        "workflow": workflow,
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
