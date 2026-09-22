from datetime import datetime

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: str
    type: str  # "job_match" | "application_update" | "message"
    title: str
    content: str
    is_read: bool = False
    created_at: datetime | None = None


class NotificationListResponse(BaseModel):
    notifications: list[NotificationOut]
    unread_count: int
