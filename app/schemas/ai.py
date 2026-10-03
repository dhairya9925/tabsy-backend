from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class AIParseRequest(BaseModel):
    """Text-based expense parse request."""
    model_config = ConfigDict(extra="ignore")

    text: Optional[str] = Field(default=None, max_length=500)
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)


class AIParseResponse(BaseModel):
    """Structured response from AI expense parsing."""
    model_config = ConfigDict(from_attributes=True)

    status: Literal["confirmed", "needs_clarification", "error"]
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    # Parsed expense data (populated when status == "confirmed")
    expense_type: Optional[Literal["personal", "group", "friend"]] = None
    amount: Optional[float] = None
    category: Optional[str] = None
    note: Optional[str] = None
    expense_date: Optional[str] = None  # ISO date string (YYYY-MM-DD)
    group_id: Optional[str] = None
    group_name: Optional[str] = None
    friend_id: Optional[str] = None
    friend_name: Optional[str] = None
    paid_by: Optional[str] = None
    split_type: Optional[Literal["equal", "full", "custom"]] = None

    # Clarification (populated when status == "needs_clarification")
    clarification_question: Optional[str] = None
    clarification_options: Optional[list[str]] = None  # Suggested answers
    ai_understanding: Optional[str] = None  # What the AI understood so far

    # Original transcription (if audio was sent) or sanitized user text
    transcribed_text: Optional[str] = None
