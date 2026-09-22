"""Administrative controls for the Wazifny platform."""
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query
from app.db.mongodb import get_database
from app.utils.deps import require_role

router = APIRouter()

async def _user_out(db, user: dict) -> dict:
    user_id = str(user["_id"])
    collection = db.talent_profiles if user.get("role") == "talent" else db.employer_profiles
    profile = await collection.find_one({"user_id": user_id}) or {}
    return {"id": user_id, "full_name": user.get("full_name", ""), "email": user.get("email", ""), "role": user.get("role", ""), "location": profile.get("city") or profile.get("location") or "Not provided", "is_blocked": bool(user.get("is_blocked")), "blocked_reason": user.get("blocked_reason"), "created_at": user.get("created_at")}

@router.get("/overview")
async def overview(current_user: dict = Depends(require_role("admin"))) -> dict:
    db = get_database()
    total_users = await db.users.count_documents({})
    recent_docs = await db.users.find({}).sort("created_at", -1).limit(5).to_list(5)
    locations = set()
    async for profile in db.talent_profiles.find({}, {"country": 1}):
        if profile.get("country"): locations.add(profile["country"])
    async for profile in db.employer_profiles.find({}, {"location": 1}):
        if profile.get("location"): locations.add(profile["location"])
    return {"total_users": total_users, "talents": await db.users.count_documents({"role": "talent", "is_blocked": {"$ne": True}}), "employers": await db.users.count_documents({"role": "employer", "is_blocked": {"$ne": True}}), "blocked_users": await db.users.count_documents({"is_blocked": True}), "jobs": await db.jobs.count_documents({}), "applications": await db.applications.count_documents({}), "active_subscriptions": await db.employer_subscriptions.count_documents({"status": "active"}), "countries_reached": len(locations), "pending_jobs": await db.jobs.count_documents({"status": "pending"}), "recent_users": [await _user_out(db, doc) for doc in recent_docs]}

@router.get("/users")
async def list_users(q: str | None = Query(None), role: str | None = Query(None), blocked: bool | None = Query(None), current_user: dict = Depends(require_role("admin"))) -> list[dict]:
    db = get_database(); query: dict = {}
    if role in {"talent", "employer", "admin"}: query["role"] = role
    if blocked is not None: query["is_blocked"] = blocked
    if q and q.strip(): query["$or"] = [{"full_name": {"$regex": q.strip(), "$options": "i"}}, {"email": {"$regex": q.strip(), "$options": "i"}}]
    docs = await db.users.find(query).sort("created_at", -1).limit(200).to_list(200)
    return [await _user_out(db, doc) for doc in docs]

@router.patch("/users/{user_id}/block")
async def set_user_blocked(user_id: str, payload: dict, current_user: dict = Depends(require_role("admin"))) -> dict:
    if user_id == current_user["id"]: raise HTTPException(status_code=400, detail="You cannot block your own admin account")
    try: oid = ObjectId(user_id)
    except InvalidId as exc: raise HTTPException(status_code=404, detail="User not found") from exc
    db = get_database(); user = await db.users.find_one({"_id": oid})
    if not user: raise HTTPException(status_code=404, detail="User not found")
    blocked = bool(payload.get("blocked")); reason = str(payload.get("reason", "Administrative action")).strip()[:200]
    await db.users.update_one({"_id": oid}, {"$set": {"is_blocked": blocked, "blocked_reason": reason if blocked else None}})
    user.update({"is_blocked": blocked, "blocked_reason": reason if blocked else None})
    return await _user_out(db, user)

@router.get("/jobs/moderation")
async def moderation_jobs(current_user: dict = Depends(require_role("admin"))) -> list[dict]:
    db = get_database(); docs = await db.jobs.find({}).sort("posted_at", -1).limit(200).to_list(200)
    return [{"id": str(job["_id"]), "title": job.get("title", ""), "company_name": job.get("company_name"), "location": job.get("location"), "category": job.get("category"), "description": job.get("description", ""), "requirements": job.get("requirements", []), "status": job.get("status", "pending"), "posted_at": job.get("posted_at")} for job in docs]

@router.patch("/jobs/{job_id}/status")
async def set_job_status(job_id: str, payload: dict, current_user: dict = Depends(require_role("admin"))) -> dict:
    status = payload.get("status")
    if status not in {"active", "removed", "closed", "pending"}: raise HTTPException(status_code=422, detail="Invalid job status")
    try: oid = ObjectId(job_id)
    except InvalidId as exc: raise HTTPException(status_code=404, detail="Job not found") from exc
    db = get_database(); result = await db.jobs.update_one({"_id": oid}, {"$set": {"status": status}})
    if not result.matched_count: raise HTTPException(status_code=404, detail="Job not found")
    return {"id": job_id, "status": status}
