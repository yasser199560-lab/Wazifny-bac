from datetime import datetime, timezone
import logging
import mimetypes

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.db.mongodb import get_database
from app.schemas.application import (
    AiScreeningResult,
    ApplicationCreate,
    ApplicationOut,
    ApplicationStatusUpdate,
)
from app.services.matching_service import get_match_scores
from app.services.ai_service import ai_complete, extract_json
from app.services.notification_service import (
    create_notification,
    send_application_confirmation_email,
    send_new_applicant_email,
)
from app.utils.deps import require_role

router = APIRouter()
logger = logging.getLogger("wazifny.applications")


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


async def _talent_summary(db, talent_id: str) -> dict:
    """Everything an employer needs to evaluate an applicant — this is what
    "the talent's info is sent to the employer" means in practice: their
    full profile (skills, education, experience) is one call away as soon
    as they apply, not buried or partial."""
    user = await db.users.find_one({"_id": ObjectId(talent_id)})
    profile = await db.talent_profiles.find_one({"user_id": talent_id}) or {}
    skills = [
        s["skill_name"] async for s in db.talent_skills.find({"talent_id": talent_id}) if s.get("skill_name")
    ]
    education = [
        {"degree": e.get("degree"), "institution": e.get("institution")}
        async for e in db.educations.find({"talent_id": talent_id})
    ]
    experience = [
        {"job_title": e.get("job_title"), "company_name": e.get("company_name")}
        async for e in db.experiences.find({"talent_id": talent_id})
    ]
    return {
        "talent_id": talent_id,
        "full_name": user.get("full_name") if user else "Unknown",
        "email": user.get("email") if user else None,
        "phone": profile.get("phone"),
        "headline": profile.get("headline"),
        "city": profile.get("city"),
        "country": profile.get("country"),
        "skills": skills,
        "education": education,
        "experience": experience,
        "cv_filename": profile.get("cv_filename"),
        "cv_available": bool(profile.get("cv_file_id")),
    }


@router.post("", response_model=ApplicationOut, status_code=201)
async def apply_to_job(
    payload: ApplicationCreate, current_user: dict = Depends(require_role("talent"))
) -> dict:
    """POST /applications — one-click apply. Talent-only: applying requires
    a signed-in account (guests are sent through registration first, see the
    frontend's register-then-apply flow).

    On success: the employer gets an in-app notification + email containing
    the applicant's name, match score, and a link to their full profile
    (skills/education/experience) — and the talent gets a confirmation
    email. Both are best-effort; a delivery failure never fails the apply
    itself.
    """
    db = get_database()
    talent_id = current_user["id"]

    try:
        job = await db.jobs.find_one({"_id": ObjectId(payload.job_id)})
    except InvalidId:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != "active":
        raise HTTPException(status_code=400, detail="This job is no longer accepting applications")

    existing = await db.applications.find_one({"talent_id": talent_id, "job_id": payload.job_id})
    if existing:
        raise HTTPException(status_code=409, detail="You've already applied to this job")

    doc = {
        "talent_id": talent_id,
        "job_id": payload.job_id,
        "applied_via": job.get("application_method", "in_platform"),
        "status": "pending",
        "applied_at": datetime.now(timezone.utc),
    }
    result = await db.applications.insert_one(doc)
    out = _serialize({**doc, "_id": result.inserted_id})
    out["job_title"] = job.get("title")
    out["company_name"] = job.get("company_name")
    scores = await get_match_scores(db, talent_id, [payload.job_id])
    match_score = scores.get(payload.job_id)
    out["match_score"] = match_score

    # Send the talent's receipt independently of employer notifications.
    # A legacy job with a malformed employer record must not stop this email.
    try:
        await send_application_confirmation_email(
            current_user["email"], current_user.get("full_name", "there"),
            job["title"], job.get("company_name"),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not send application confirmation for job %s", payload.job_id)

    try:
        employer_user = await db.users.find_one({"_id": ObjectId(job["employer_id"])})
        talent_name = current_user.get("full_name", "A candidate")
        score_text = f" — {match_score}% match" if match_score is not None else ""

        if employer_user:
            await create_notification(
                db,
                str(employer_user["_id"]),
                "application",
                "New Application Received",
                f"{talent_name} applied to {job['title']}{score_text}",
            )
            await send_new_applicant_email(
                employer_user["email"], employer_user.get("full_name", ""),
                talent_name, job["title"], match_score,
            )

    except Exception:  # noqa: BLE001
        pass  # notifications/email are best-effort — never fail the apply itself

    return out


@router.get("/me", response_model=list[ApplicationOut])
async def my_applications(current_user: dict = Depends(require_role("talent"))) -> list[dict]:
    """GET /applications/me — the current talent's application history."""
    db = get_database()
    talent_id = current_user["id"]
    cursor = db.applications.find({"talent_id": talent_id}).sort("applied_at", -1)

    app_docs = [doc async for doc in cursor]
    job_ids = [a["job_id"] for a in app_docs]
    scores = await get_match_scores(db, talent_id, job_ids)

    object_ids = []
    for job_id in job_ids:
        try:
            object_ids.append(ObjectId(job_id))
        except InvalidId:
            continue
    jobs = {
        str(job["_id"]): job
        async for job in db.jobs.find({"_id": {"$in": object_ids}})
    } if object_ids else {}

    results = []
    for app_doc in app_docs:
        out = _serialize(app_doc)
        job = jobs.get(app_doc["job_id"])
        out["job_title"] = job.get("title") if job else None
        out["company_name"] = job.get("company_name") if job else None
        out["employer_id"] = job.get("employer_id") if job else None
        out["match_score"] = scores.get(app_doc["job_id"])
        results.append(out)
    return results


@router.get("/job/{job_id}")
async def applicants_for_job(job_id: str, current_user: dict = Depends(require_role("employer"))) -> list[dict]:
    """GET /applications/job/{job_id} — every applicant for one of the
    current employer's own jobs, each with their full profile summary and
    match score (powers the Applicants page)."""
    db = get_database()
    try:
        job = await db.jobs.find_one({"_id": ObjectId(job_id)})
    except InvalidId:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job or job.get("employer_id") != current_user["id"]:
        raise HTTPException(status_code=404, detail="Job not found")

    apps = await db.applications.find({"job_id": job_id}).sort("applied_at", -1).to_list(length=200)

    results = []
    for app_doc in apps:
        summary = await _talent_summary(db, app_doc["talent_id"])
        score_map = await get_match_scores(db, app_doc["talent_id"], [job_id])
        results.append(
            {
                "application_id": str(app_doc["_id"]),
                "status": app_doc.get("status", "pending"),
                "applied_at": app_doc.get("applied_at"),
                "match_score": score_map.get(job_id),
                **summary,
            }
        )
    return results


@router.patch("/{application_id}", response_model=ApplicationOut)
async def update_application_status(
    application_id: str,
    payload: ApplicationStatusUpdate,
    current_user: dict = Depends(require_role("employer")),
) -> dict:
    """PATCH /applications/{id} — employer updates an applicant's status.
    Notifies the talent in-app when their status changes."""
    db = get_database()
    try:
        app_doc = await db.applications.find_one({"_id": ObjectId(application_id)})
    except InvalidId:
        raise HTTPException(status_code=404, detail="Application not found")
    if not app_doc:
        raise HTTPException(status_code=404, detail="Application not found")

    try:
        job = await db.jobs.find_one({"_id": ObjectId(app_doc["job_id"])})
    except InvalidId:
        job = None
    if not job or job.get("employer_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not your job posting")

    await db.applications.update_one(
        {"_id": app_doc["_id"]}, {"$set": {"status": payload.status}}
    )

    try:
        await create_notification(
            db,
            app_doc["talent_id"],
            "application_update",
            "Application Update",
            f"{job.get('company_name', 'An employer')} moved your application to "
            f"{payload.status.capitalize()}",
        )
    except Exception:  # noqa: BLE001
        pass

    out = _serialize({**app_doc, "status": payload.status})
    out["job_title"] = job.get("title")
    out["company_name"] = job.get("company_name")
    scores = await get_match_scores(db, app_doc["talent_id"], [app_doc["job_id"]])
    out["match_score"] = scores.get(app_doc["job_id"])
    return out


@router.get("/{application_id}/cv")
async def view_applicant_cv(
    application_id: str, current_user: dict = Depends(require_role("employer"))
) -> Response:
    """Return an applicant's uploaded CV to the owner of the applied job.

    The application id, rather than a talent id, is intentionally used as the
    URL identifier: it lets us verify the requesting employer owns the exact
    job application before any private document is released.
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

    profile = await db.talent_profiles.find_one({"user_id": application["talent_id"]}) or {}
    file_id = profile.get("cv_file_id")
    filename = profile.get("cv_filename") or "candidate-cv"
    if not file_id:
        raise HTTPException(status_code=404, detail="This candidate has not uploaded a CV")

    try:
        from motor.motor_asyncio import AsyncIOMotorGridFSBucket

        bucket = AsyncIOMotorGridFSBucket(db)
        stream = await bucket.open_download_stream(ObjectId(file_id))
        content = await stream.read()
    except (InvalidId, Exception) as exc:
        # Invalid/missing GridFS entries should not expose implementation
        # details to an employer. The document may have been removed after a
        # re-upload or a legacy profile may only retain its filename.
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=404, detail="The uploaded CV is no longer available") from exc

    media_type = stream.metadata.get("content_type") if stream.metadata else None
    media_type = media_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    safe_filename = filename.replace("\r", "").replace("\n", "").replace('"', "")
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{safe_filename}"'},
    )


@router.post("/{application_id}/ai-screen", response_model=AiScreeningResult)
async def ai_screen_application(
    application_id: str, current_user: dict = Depends(require_role("employer"))
) -> dict:
    """POST /applications/{id}/ai-screen — BRD §4.5 (AI-assisted resume
    screening), brought forward from "Phase 2 planned" on request.

    The AI compares the applicant's real profile (skills/education/
    experience — the same data shown on the Applicants page) against the
    job's real requirements/description, and returns a recommendation with
    reasoning grounded only in that comparison — it's told explicitly not
    to invent skills, experience, or facts not present in either document.

    This is assistive, not autonomous: it never changes the application's
    status itself. The employer sees the suggestion and reasoning, and
    applies it (or not) via the existing PATCH /applications/{id} endpoint
    — a human always makes the final call on a hiring decision, which
    matters both practically (the AI can be wrong) and for basic fairness/
    accountability in something as consequential as screening candidates.
    """
    db = get_database()
    try:
        app_doc = await db.applications.find_one({"_id": ObjectId(application_id)})
    except InvalidId:
        raise HTTPException(status_code=404, detail="Application not found")
    if not app_doc:
        raise HTTPException(status_code=404, detail="Application not found")

    try:
        job = await db.jobs.find_one({"_id": ObjectId(app_doc["job_id"])})
    except InvalidId:
        job = None
    if not job or job.get("employer_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not your job posting")

    summary = await _talent_summary(db, app_doc["talent_id"])

    system_prompt = (
        "You are an AI hiring-assistant for Wazifny. Compare ONE real "
        "candidate's profile against ONE real job's requirements — use "
        "ONLY the facts given below, never invent skills, experience, "
        "education, or requirements not explicitly present. Give a "
        "balanced assessment: note genuine strengths (skills/experience "
        "that match) and genuine gaps (requirements the candidate's "
        "profile doesn't show), then a recommendation. This is advisory "
        "only — a human makes the final decision. Respond with ONLY a "
        "JSON object, no prose, in this exact shape: "
        '{"recommendation": "shortlist" | "consider" | "reject", '
        '"reasoning": "<2-3 sentences>", "strengths": ["<short phrase>", ...], '
        '"gaps": ["<short phrase>", ...]}'
    )
    user_prompt = (
        f"Job: {job['title']}\n"
        f"Requirements: {job.get('requirements') or 'Not specified'}\n"
        f"Description: {(job.get('description') or '')[:500]}\n\n"
        f"Candidate: {summary['full_name']}\n"
        f"Headline: {summary.get('headline') or 'Not specified'}\n"
        f"Skills: {summary['skills'] or 'None listed'}\n"
        f"Education: {summary['education'] or 'None listed'}\n"
        f"Experience: {summary['experience'] or 'None listed'}"
    )

    raw, provider = await ai_complete(system_prompt, user_prompt)
    if not raw:
        return {"ai_available": False}

    parsed = extract_json(raw)
    if not isinstance(parsed, dict) or parsed.get("recommendation") not in (
        "shortlist", "consider", "reject",
    ):
        return {"ai_available": False}

    def _phrases(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()][:6]

    return {
        "ai_available": True,
        "ai_provider": provider,
        "recommendation": parsed["recommendation"],
        "reasoning": str(parsed.get("reasoning", "")).strip() or None,
        "strengths": _phrases(parsed.get("strengths")),
        "gaps": _phrases(parsed.get("gaps")),
    }
