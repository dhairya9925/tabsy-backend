import json
import logging
from typing import Annotated, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.schemas.ai import AIParseResponse
from app.schemas.envelope import ResponseEnvelope
from app.services.ai_service import parse_expense_intent, transcribe_audio

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/parse-expense",
    response_model=ResponseEnvelope[AIParseResponse],
    status_code=status.HTTP_200_OK,
    summary="Parse expense intent from voice audio or text",
    description=(
        "Accepts either an audio file (speech-to-text via Whisper) or raw text. "
        "Returns a structured expense intent or a clarification question if ambiguous. "
        "Supports both multipart/form-data and application/json."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "text": {
                                "type": "string",
                                "description": "Natural language expense description (max 500 characters)",
                            },
                            "audio": {
                                "type": "string",
                                "format": "binary",
                                "description": "Audio recording file (.m4a, .mp3, .wav)",
                            },
                            "conversation_history": {
                                "type": "string",
                                "description": "JSON-encoded array of previous conversation messages for multi-turn disambiguation",
                            },
                        },
                    }
                },
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "conversation_history": {
                                "type": "array",
                                "items": {"type": "object"},
                            },
                        },
                    }
                },
            }
        }
    },
)
async def parse_expense(
    request: Request,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[AIParseResponse]:
    """
    Parse a user's expense intent from voice or text.
    - Send `audio` (multipart) for voice input
    - Send `text` (form or JSON) for typed input
    - Send `conversation_history` for multi-turn disambiguation
    """
    content_type = request.headers.get("content-type", "")

    user_text: Optional[str] = None
    audio_file: Optional[UploadFile] = None
    conversation_history: list[dict[str, Any]] = []
    input_type = "text"
    transcribed_text: Optional[str] = None

    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            body = {}
        user_text = body.get("text")
        raw_hist = body.get("conversation_history")
        if isinstance(raw_hist, list):
            conversation_history = raw_hist
    else:
        # Form / Multipart
        form = await request.form()
        raw_text = form.get("text")
        if isinstance(raw_text, str):
            user_text = raw_text

        raw_audio = form.get("audio")
        if raw_audio is not None and hasattr(raw_audio, "read") and hasattr(raw_audio, "filename"):
            audio_file = raw_audio

        raw_hist = form.get("conversation_history")
        if raw_hist:
            if isinstance(raw_hist, str):
                try:
                    parsed = json.loads(raw_hist)
                    if isinstance(parsed, list):
                        conversation_history = parsed
                except Exception:
                    conversation_history = []
            elif isinstance(raw_hist, list):
                conversation_history = raw_hist

    # Process audio if provided
    if audio_file is not None and audio_file.filename:
        input_type = "audio"
        try:
            transcribed_text = await transcribe_audio(audio_file)
            user_text = transcribed_text
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Failed to transcribe audio file")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unable to process audio recording: {str(e)}",
            )

    # Fallback to empty string if no text provided
    if user_text is None:
        user_text = ""

    parsed_result = await parse_expense_intent(
        user_input=user_text,
        conversation_history=conversation_history,
        user_id=current_user.id,
        db=db,
        input_type=input_type,
        transcribed_text=transcribed_text,
    )

    return ResponseEnvelope(data=parsed_result)
