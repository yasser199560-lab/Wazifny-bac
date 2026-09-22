from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException

from app.db.mongodb import get_database
from app.schemas.notification import NotificationListResponse
from app.utils.deps import get_current_user

router = APIRouter()


def _serialize(doc: dict) -> dict:
    out = dict(doc)
    out["id"] = str(out.pop("_id"))
    out.pop("user_id", None)
    return out


@router.get("", response_model=NotificationListResponse)
async def list_notifications(current_user: dict = Depends(get_current_user)) -> dict:
    """GET /notifications — the current user's notifications, newest first."""
    db = get_database()
    cursor = db.notifications.find({"user_id": current_user["id"]}).sort("created_at", -1).limit(50)
    notifications = [_serialize(doc) async for doc in cursor]
    unread_count = sum(1 for n in notifications if not n.get("is_read"))
    return {"notifications": notifications, "unread_count": unread_count}


@router.patch("/{notification_id}/read", status_code=204)
async def mark_read(notification_id: str, current_user: dict = Depends(get_current_user)) -> None:
    db = get_database()
    try:
        oid = ObjectId(notification_id)
    except InvalidId:
        raise HTTPException(status_code=404, detail="Not found")
    await db.notifications.update_one(
        {"_id": oid, "user_id": current_user["id"]}, {"$set": {"is_read": True}}
    )


@router.post("/mark-all-read", status_code=204)
async def mark_all_read(current_user: dict = Depends(get_current_user)) -> None:
    db = get_database()
    await db.notifications.update_many(
        {"user_id": current_user["id"], "is_read": False}, {"$set": {"is_read": True}}
    )
