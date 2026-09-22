from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException

from app.db.mongodb import get_database
from app.schemas.message import (
    ConversationCreate,
    ConversationOut,
    MessageCreate,
    MessageOut,
    SuggestedRepliesResponse,
)
from app.services.ai_service import ai_complete, extract_json
from app.utils.deps import get_current_user, require_role

router = APIRouter()


async def _other_party(db, conv: dict, my_id: str) -> tuple[str, str, str]:
    """Returns (other_party_id, other_party_name, other_party_role).

    When the other party is an employer, the talent sees a company-branded
    name ("TechCorp Lebanon HR") rather than the individual account
    holder's personal name — matching how a real hiring platform presents
    employer contacts."""
    other_id = conv["employer_id"] if conv["talent_id"] == my_id else conv["talent_id"]
    other_role = "employer" if other_id == conv["employer_id"] else "talent"

    if other_role == "employer":
        profile = await db.employer_profiles.find_one({"user_id": other_id})
        company_name = profile.get("company_name") if profile else None
        name = f"{company_name} HR" if company_name else "Employer"
    else:
        user = await db.users.find_one({"_id": ObjectId(other_id)})
        name = user.get("full_name", "Talent") if user else "Talent"

    return other_id, name, other_role


async def _assert_participant(conv: dict, my_id: str) -> None:
    if my_id not in (conv.get("talent_id"), conv.get("employer_id")):
        raise HTTPException(status_code=403, detail="You're not part of this conversation")


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(current_user: dict = Depends(get_current_user)) -> list[dict]:
    """GET /messages/conversations — every conversation the current user
    (talent or employer) is part of, most recently active first."""
    db = get_database()
    my_id = current_user["id"]

    cursor = db.conversations.find(
        {"$or": [{"talent_id": my_id}, {"employer_id": my_id}]}
    ).sort("updated_at", -1)

    results = []
    async for conv in cursor:
        other_id, other_name, other_role = await _other_party(db, conv, my_id)
        last_msg = await db.messages.find_one(
            {"conversation_id": str(conv["_id"])}, sort=[("sent_at", -1)]
        )
        unread = await db.messages.count_documents(
            {"conversation_id": str(conv["_id"]), "sender_id": {"$ne": my_id}, "is_read": False}
        )
        results.append(
            {
                "id": str(conv["_id"]),
                "other_party_id": other_id,
                "other_party_name": other_name,
                "other_party_role": other_role,
                "last_message": last_msg["content"] if last_msg else None,
                "last_message_at": last_msg["sent_at"] if last_msg else None,
                "unread_count": unread,
            }
        )
    return results


@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def start_conversation(
    payload: ConversationCreate, current_user: dict = Depends(require_role("talent"))
) -> dict:
    """POST /messages/conversations — talent starts (or reuses) a
    conversation with an employer."""
    db = get_database()
    talent_id = current_user["id"]

    existing = await db.conversations.find_one(
        {"talent_id": talent_id, "employer_id": payload.employer_id}
    )
    if existing:
        conv = existing
    else:
        now = datetime.now(timezone.utc)
        doc = {
            "talent_id": talent_id,
            "employer_id": payload.employer_id,
            "created_at": now,
            "updated_at": now,
        }
        result = await db.conversations.insert_one(doc)
        conv = {**doc, "_id": result.inserted_id}

    other_id, other_name, other_role = await _other_party(db, conv, talent_id)
    return {
        "id": str(conv["_id"]),
        "other_party_id": other_id,
        "other_party_name": other_name,
        "other_party_role": other_role,
        "last_message": None,
        "last_message_at": None,
        "unread_count": 0,
    }


@router.post("/applications/{application_id}/conversation", response_model=ConversationOut, status_code=201)
async def employer_start_applicant_conversation(
    application_id: str, current_user: dict = Depends(require_role("employer"))
) -> dict:
    """Create or reuse a conversation with an applicant for the employer's job.

    Employers can only initiate contact with people who applied to one of
    their own jobs; this prevents an account from messaging arbitrary talent
    profiles.
    """
    db = get_database()
    try:
        application = await db.applications.find_one({"_id": ObjectId(application_id)})
    except InvalidId:
        application = None
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    try:
        job = await db.jobs.find_one({"_id": ObjectId(application["job_id"])})
    except InvalidId:
        job = None
    if not job or job.get("employer_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not your job posting")

    talent_id = application["talent_id"]
    existing = await db.conversations.find_one(
        {"talent_id": talent_id, "employer_id": current_user["id"]}
    )
    if existing:
        conv = existing
    else:
        now = datetime.now(timezone.utc)
        doc = {
            "talent_id": talent_id,
            "employer_id": current_user["id"],
            "created_at": now,
            "updated_at": now,
        }
        result = await db.conversations.insert_one(doc)
        conv = {**doc, "_id": result.inserted_id}

    other_id, other_name, other_role = await _other_party(db, conv, current_user["id"])
    return {
        "id": str(conv["_id"]),
        "other_party_id": other_id,
        "other_party_name": other_name,
        "other_party_role": other_role,
        "last_message": None,
        "last_message_at": None,
        "unread_count": 0,
    }


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def get_messages(
    conversation_id: str, current_user: dict = Depends(get_current_user)
) -> list[dict]:
    """GET /messages/conversations/{id}/messages — full message history,
    oldest first. Opening a conversation marks the other party's messages
    as read."""
    db = get_database()
    try:
        conv = await db.conversations.find_one({"_id": ObjectId(conversation_id)})
    except InvalidId:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await _assert_participant(conv, current_user["id"])

    await db.messages.update_many(
        {
            "conversation_id": conversation_id,
            "sender_id": {"$ne": current_user["id"]},
            "is_read": False,
        },
        {"$set": {"is_read": True}},
    )

    cursor = db.messages.find({"conversation_id": conversation_id}).sort("sent_at", 1)
    results = []
    async for doc in cursor:
        out = dict(doc)
        out["id"] = str(out.pop("_id"))
        results.append(out)
    return results


@router.post("/conversations/{conversation_id}/messages", response_model=MessageOut, status_code=201)
async def send_message(
    conversation_id: str,
    payload: MessageCreate,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """POST /messages/conversations/{id}/messages — send a message."""
    db = get_database()
    try:
        conv = await db.conversations.find_one({"_id": ObjectId(conversation_id)})
    except InvalidId:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await _assert_participant(conv, current_user["id"])

    now = datetime.now(timezone.utc)
    doc = {
        "conversation_id": conversation_id,
        "sender_id": current_user["id"],
        "content": payload.content,
        "sent_at": now,
        "is_read": False,
    }
    result = await db.messages.insert_one(doc)
    await db.conversations.update_one({"_id": conv["_id"]}, {"$set": {"updated_at": now}})

    out = dict(doc)
    out["id"] = str(result.inserted_id)
    return out


@router.get(
    "/conversations/{conversation_id}/suggest-replies",
    response_model=SuggestedRepliesResponse,
)
async def suggest_replies(
    conversation_id: str, current_user: dict = Depends(get_current_user)
) -> dict:
    """GET /messages/conversations/{id}/suggest-replies — 2-3 short AI-
    drafted reply options based on the real conversation so far, written in
    the current user's voice (talent or employer). The suggestions are
    shown as editable drafts, never auto-sent — the person still reviews
    and clicks Send themselves.

    If both AI providers are down, returns `ai_available: false` with an
    empty list rather than passing off generic canned text as "AI
    suggested" — that would be misleading."""
    db = get_database()
    try:
        conv = await db.conversations.find_one({"_id": ObjectId(conversation_id)})
    except InvalidId:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await _assert_participant(conv, current_user["id"])

    history = await db.messages.find({"conversation_id": conversation_id}).sort(
        "sent_at", -1
    ).limit(10).to_list(length=10)
    history.reverse()

    if not history:
        return {"ai_available": False, "suggestions": []}

    my_id = current_user["id"]
    transcript = [
        f"{'Me' if m['sender_id'] == my_id else 'Them'}: {m['content']}" for m in history
    ]
    my_role = current_user.get("role", "user")

    system_prompt = (
        f"You draft short reply suggestions for a {my_role} on a job "
        "platform's chat, replying to the other person in this real "
        "conversation. Use ONLY the conversation given — never invent facts "
        "about jobs, candidates, or companies not mentioned. Write in a "
        "professional, friendly tone, 1-2 sentences each. Respond with "
        'ONLY a JSON array of 2-3 short strings, no prose, e.g. '
        '["Sure, Tuesday at 2pm works for me.", "Could we do a call instead?"]'
    )
    user_prompt = "Conversation so far:\n" + "\n".join(transcript)

    raw, provider = await ai_complete(system_prompt, user_prompt)
    if not raw:
        return {"ai_available": False, "suggestions": []}

    parsed = extract_json(raw)
    if not isinstance(parsed, list):
        return {"ai_available": False, "suggestions": []}

    suggestions = [str(s).strip() for s in parsed if isinstance(s, (str, int, float)) and str(s).strip()][:3]
    if not suggestions:
        return {"ai_available": False, "suggestions": []}

    return {"ai_available": True, "ai_provider": provider, "suggestions": suggestions}
