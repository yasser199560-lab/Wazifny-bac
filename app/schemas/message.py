from datetime import datetime

from pydantic import BaseModel, Field


class ConversationOut(BaseModel):
    id: str
    other_party_id: str
    other_party_name: str
    other_party_role: str  # "talent" | "employer"
    last_message: str | None = None
    last_message_at: datetime | None = None
    unread_count: int = 0


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    content: str
    sent_at: datetime | None = None
    is_read: bool = False


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


class ConversationCreate(BaseModel):
    """Talent-only: start (or reuse) a conversation with an employer, e.g.
    from a job posting's "Message employer" action."""

    employer_id: str


class SuggestedRepliesResponse(BaseModel):
    ai_available: bool
    ai_provider: str | None = None
    suggestions: list[str] = []
