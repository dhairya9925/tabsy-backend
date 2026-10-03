import logging
from typing import Annotated
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.schemas.ai import AIParseRequest, AIParseResponse
from app.schemas.envelope import ResponseEnvelope
from app.services.ai_service import parse_expense_intent

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/parse-expense",
    response_model=ResponseEnvelope[AIParseResponse],
    status_code=status.HTTP_200_OK,
    summary="Parse expense intent from text",
    description=(
        "Accepts natural language text (typed or recognized via native device STT). "
        "Returns a structured expense intent or a clarification question if ambiguous."
    ),
)
async def parse_expense(
    payload: AIParseRequest,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResponseEnvelope[AIParseResponse]:
    """
    Parse a user's expense intent from text.
    - Send `text` for typed or native speech-recognized input
    - Send `conversation_history` for multi-turn disambiguation
    """
    user_text = payload.text or ""
    conversation_history = payload.conversation_history or []

    parsed_result = await parse_expense_intent(
        user_input=user_text,
        conversation_history=conversation_history,
        user_id=current_user.id,
        db=db,
        input_type="text",
        transcribed_text=user_text,
    )

    return ResponseEnvelope(data=parsed_result)
