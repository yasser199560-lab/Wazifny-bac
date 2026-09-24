from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.db.mongodb import get_database
from app.schemas.employer import (
    CandidateSummary,
    CompanyProfileOut,
    CompanyProfileUpdate,
    EmployerDashboardOut,
    SubscriptionOut,
    SubscriptionSwitchRequest,
)
from app.utils.deps import require_role

router = APIRouter()

_COMPANY_IMAGE_MAX_BYTES = 3 * 1024 * 1024
_COMPANY_IMAGE_TYPES = {
    "image/jpeg": ("jpg", lambda data: data.startswith(b"\xff\xd8\xff")),
    "image/png": ("png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/webp": ("webp", lambda data: data.startswith(b"RIFF") and data[8:12] == b"WEBP"),
}


def _company_image_url(employer_id: str, kind: str) -> str:
    return f"/api/v1/employers/{employer_id}/media/{kind}"


@router.post("/me/media/{kind}")
async def upload_company_image(
    kind: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(require_role("employer")),
) -> dict:
    if kind not in {"logo", "banner"}:
        raise HTTPException(status_code=404, detail="Image type not found")
    content_type = file.content_type or ""
    image_type = _COMPANY_IMAGE_TYPES.get(content_type)
    if image_type is None:
        raise HTTPException(status_code=400, detail="Upload a JPEG, PNG, or WebP image")

    content = await file.read(_COMPANY_IMAGE_MAX_BYTES + 1)
    if len(content) > _COMPANY_IMAGE_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Image must be 3 MB or smaller")
    if not image_type[1](content):
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid image")

    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    db = get_database()
    profile = await db.employer_profiles.find_one({"user_id": current_user["id"]}) or {}
    field = f"{kind}_file_id"
    bucket = AsyncIOMotorGridFSBucket(db)
    previous_id = profile.get(field)
    file_id = await bucket.upload_from_stream(
        f"company-{kind}.{image_type[0]}",
        content,
        metadata={"employer_id": current_user["id"], "kind": kind, "content_type": content_type},
    )
    url = _company_image_url(current_user["id"], kind)
    await db.employer_profiles.update_one(
        {"user_id": current_user["id"]},
        {"$set": {field: str(file_id), f"{kind}_url": url}},
        upsert=True,
    )
    if previous_id:
        try:
            await bucket.delete(ObjectId(previous_id))
        except Exception:
            pass
    if kind == "logo":
        await db.jobs.update_many(
            {"employer_id": current_user["id"]},
            {"$set": {"company_logo_url": url}},
        )
    return {"url": url}


@router.get("/{employer_id}/media/{kind}")
async def get_company_image(employer_id: str, kind: str) -> Response:
    if kind not in {"logo", "banner"}:
        raise HTTPException(status_code=404, detail="Image not found")
    db = get_database()
    profile = await db.employer_profiles.find_one({"user_id": employer_id})
    if not profile or not profile.get(f"{kind}_file_id"):
        raise HTTPException(status_code=404, detail="Image not found")

    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    try:
        bucket = AsyncIOMotorGridFSBucket(db)
        image = await bucket.open_download_stream(ObjectId(profile[f"{kind}_file_id"]))
        content = await image.read()
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Image not found") from exc
    media_type = (image.metadata or {}).get("content_type", "application/octet-stream")
    return Response(
        content,
        media_type=media_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )

PLAN_CATALOG = {
    "starter": {
        "name": "Starter", "price": 49, "duration_days": 30,
        "features": ["5 job postings/month", "Basic applicant management", "Email support", "Standard job visibility"],
    },
    "growth": {
        "name": "Growth", "price": 99, "duration_days": 30,
        "features": ["20 job postings/month", "Advanced applicant management", "Saved candidate profiles (up to 50)", "Priority job visibility", "In-app messaging", "Priority email support"],
    },
    "enterprise": {
        "name": "Enterprise", "price": 249, "duration_days": 30,
        "features": ["Unlimited job postings", "Full applicant management suite", "Unlimited saved candidates", "Featured job visibility"],
    },
}


async def _talent_summary(db, talent_id: str, job_ids: list[str] | None = None) -> dict:
    user = await db.users.find_one({"_id": ObjectId(talent_id)})
    profile = await db.talent_profiles.find_one({"user_id": talent_id}) or {}
    skills = [
        s["skill_name"] async for s in db.talent_skills.find({"talent_id": talent_id}) if s.get("skill_name")
    ]
    match_score = None
    if job_ids:
        cursor = db.job_matches.find(
            {"talent_id": talent_id, "job_id": {"$in": job_ids}}
        ).sort("match_score", -1).limit(1)
        top = await cursor.to_list(length=1)
        if top:
            match_score = top[0]["match_score"]
    return {
        "talent_id": talent_id,
        "full_name": user.get("full_name") if user else "Unknown",
        "headline": profile.get("headline"),
        "city": profile.get("city"),
        "skills": skills,
        "match_score": match_score,
    }


@router.get("/me", response_model=CompanyProfileOut)
async def get_company_profile(current_user: dict = Depends(require_role("employer"))) -> dict:
    """GET /employers/me — the current employer's company profile."""
    db = get_database()
    profile = await db.employer_profiles.find_one({"user_id": current_user["id"]}) or {}
    return {
        "user_id": current_user["id"],
        "company_name": profile.get("company_name", ""),
        "logo_url": profile.get("logo_url") or (_company_image_url(current_user["id"], "logo") if profile.get("logo_file_id") else None),
        "banner_url": profile.get("banner_url") or (_company_image_url(current_user["id"], "banner") if profile.get("banner_file_id") else None),
        "website": profile.get("website"),
        "sector": profile.get("sector"),
        "workforce_size": profile.get("workforce_size"),
        "lifecycle_stage": profile.get("lifecycle_stage"),
        "description": profile.get("description"),
        "location": profile.get("location"),
        "is_hidden": profile.get("is_hidden", False),
    }


@router.put("/me", response_model=CompanyProfileOut)
async def update_company_profile(
    payload: CompanyProfileUpdate, current_user: dict = Depends(require_role("employer"))
) -> dict:
    """PUT /employers/me — update company profile fields."""
    db = get_database()
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if update:
        await db.employer_profiles.update_one(
            {"user_id": current_user["id"]}, {"$set": update}, upsert=True
        )
    return await get_company_profile(current_user)


@router.get("/dashboard", response_model=EmployerDashboardOut)
async def get_dashboard(current_user: dict = Depends(require_role("employer"))) -> dict:
    """GET /employers/dashboard — active jobs, applicants, shortlisted,
    saved candidates, recent postings, and top-matched candidates across
    all of this employer's jobs."""
    db = get_database()
    employer_id = current_user["id"]

    jobs = await db.jobs.find({"employer_id": employer_id}).sort("posted_at", -1).to_list(length=100)
    job_ids = [str(j["_id"]) for j in jobs]
    active_jobs = sum(1 for j in jobs if j.get("status") == "active")

    total_applicants = await db.applications.count_documents({"job_id": {"$in": job_ids}})
    shortlisted = await db.applications.count_documents(
        {"job_id": {"$in": job_ids}, "status": "shortlisted"}
    )
    saved_candidates = await db.saved_candidates.count_documents({"employer_id": employer_id})

    recent_jobs = []
    for job in jobs[:3]:
        count = await db.applications.count_documents({"job_id": str(job["_id"])})
        recent_jobs.append(
            {
                "id": str(job["_id"]),
                "title": job["title"],
                "status": job.get("status", "active"),
                "applicants_count": count,
                "posted_at": job.get("posted_at"),
            }
        )

    top_matches = await db.job_matches.find({"job_id": {"$in": job_ids}}).sort(
        "match_score", -1
    ).limit(3).to_list(length=3)
    top_candidates = []
    seen = set()
    for m in top_matches:
        if m["talent_id"] in seen:
            continue
        seen.add(m["talent_id"])
        user = await db.users.find_one({"_id": ObjectId(m["talent_id"])})
        profile = await db.talent_profiles.find_one({"user_id": m["talent_id"]}) or {}
        top_candidates.append(
            {
                "talent_id": m["talent_id"],
                "full_name": user.get("full_name") if user else "Unknown",
                "headline": profile.get("headline"),
                "match_score": m["match_score"],
            }
        )

    return {
        "active_jobs": active_jobs,
        "total_applicants": total_applicants,
        "shortlisted": shortlisted,
        "saved_candidates": saved_candidates,
        "recent_jobs": recent_jobs,
        "top_candidates": top_candidates,
    }


@router.post("/candidates/{talent_id}/save", status_code=201)
async def save_candidate(talent_id: str, current_user: dict = Depends(require_role("employer"))) -> dict:
    """POST /employers/candidates/{talent_id}/save — bookmark a candidate."""
    db = get_database()
    await db.saved_candidates.update_one(
        {"employer_id": current_user["id"], "talent_id": talent_id},
        {"$setOnInsert": {
            "employer_id": current_user["id"], "talent_id": talent_id,
            "saved_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return {"saved": True}


@router.delete("/candidates/{talent_id}/save", status_code=204)
async def unsave_candidate(talent_id: str, current_user: dict = Depends(require_role("employer"))) -> None:
    db = get_database()
    await db.saved_candidates.delete_one({"employer_id": current_user["id"], "talent_id": talent_id})


@router.get("/candidates/saved", response_model=list[CandidateSummary])
async def saved_candidates(current_user: dict = Depends(require_role("employer"))) -> list[dict]:
    """GET /employers/candidates/saved — bookmarked candidates, each with
    their best match score against any of this employer's jobs."""
    db = get_database()
    employer_id = current_user["id"]

    jobs = await db.jobs.find({"employer_id": employer_id}).to_list(length=200)
    job_ids = [str(j["_id"]) for j in jobs]

    saved = await db.saved_candidates.find({"employer_id": employer_id}).sort("saved_at", -1).to_list(length=100)
    return [await _talent_summary(db, s["talent_id"], job_ids) for s in saved]


@router.get("/subscription", response_model=SubscriptionOut)
async def get_subscription(current_user: dict = Depends(require_role("employer"))) -> dict:
    """GET /employers/subscription — current plan + all plans to compare.
    No real billing/payment provider is wired up — switching plans here
    updates the record instantly, same as the reference design."""
    db = get_database()
    sub = await db.employer_subscriptions.find_one({"employer_id": current_user["id"]})
    plan_id = sub["plan_id"] if sub else "starter"
    plan = PLAN_CATALOG.get(plan_id, PLAN_CATALOG["starter"])
    return {
        "current_plan": plan["name"],
        "renews_at": sub.get("end_date") if sub else None,
        "price": plan["price"],
        "features": plan["features"],
        "all_plans": [
            {"plan_id": pid, **details} for pid, details in PLAN_CATALOG.items()
        ],
    }


@router.post("/subscription/switch", response_model=SubscriptionOut)
async def switch_subscription(
    payload: SubscriptionSwitchRequest, current_user: dict = Depends(require_role("employer"))
) -> dict:
    """POST /employers/subscription/switch — instantly switch plans (no
    payment flow — this is a demo/manual-billing setup, matching the
    reference design which also has no real checkout)."""
    if payload.plan_id not in PLAN_CATALOG:
        raise HTTPException(status_code=400, detail="Unknown plan")

    db = get_database()
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    plan = PLAN_CATALOG[payload.plan_id]
    await db.employer_subscriptions.update_one(
        {"employer_id": current_user["id"]},
        {
            "$set": {
                "employer_id": current_user["id"],
                "plan_id": payload.plan_id,
                "start_date": now,
                "end_date": now + timedelta(days=plan["duration_days"]),
                "status": "active",
            }
        },
        upsert=True,
    )
    return await get_subscription(current_user)
