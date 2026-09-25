from datetime import date, datetime, timezone
import logging

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.db.mongodb import get_database
from app.schemas.job import JobOut
from app.schemas.talent import (
    AddSkillRequest,
    CvUploadResponse,
    EducationEntry,
    EducationOut,
    ExperienceEntry,
    ExperienceOut,
    PersonalInfoUpdate,
    TalentMeOut,
    TalentPreferencesOut,
    TalentPreferencesUpdate,
)
from app.services.cv_parser_service import (
    ALLOWED_CONTENT_TYPES,
    MAX_FILE_SIZE_BYTES,
    CvParsingError,
    extract_text,
    parse_cv_fields,
)
from app.services.matching_service import get_match_scores
from app.utils.deps import require_role

router = APIRouter()
logger = logging.getLogger("wazifny.cv")

# Scalar personal-info fields CV parsing is allowed to autofill.
_CV_SCALAR_FIELDS = ["phone", "city", "country", "headline"]

# Fields that count toward "profile completion" — mirrors the My Profile
# page's completion bar. dob/gender are flagged as strictly required in the
# UI; the rest still count toward the percentage but don't block completion.
_COMPLETION_FIELDS = ["city", "phone", "headline", "dob", "gender"]


def _serialize(doc: dict) -> dict:
    out = dict(doc)
    out["id"] = str(out.pop("_id"))
    out.pop("talent_id", None)
    return out


async def _compute_completion(
    db, talent_id: str, profile: dict, skills: list[str]
) -> tuple[str, int]:
    filled = sum(1 for f in _COMPLETION_FIELDS if profile.get(f))
    has_education = await db.educations.count_documents({"talent_id": talent_id}) > 0
    has_experience = await db.experiences.count_documents({"talent_id": talent_id}) > 0
    checks = [filled == len(_COMPLETION_FIELDS), has_education, has_experience, bool(skills)]
    percent = round((filled / len(_COMPLETION_FIELDS)) * 60 + sum(checks[1:]) / 3 * 40)
    status = "complete" if all(checks) else "incomplete"
    return status, min(percent, 100)


@router.get("/me", response_model=TalentMeOut)
async def get_my_talent_profile(current_user: dict = Depends(require_role("talent"))) -> dict:
    """GET /talents/me — full talent profile: personal info, education,
    experience, skills, preferences, and a computed completion percentage."""
    db = get_database()
    talent_id = current_user["id"]

    profile = await db.talent_profiles.find_one({"user_id": talent_id}) or {}
    prefs = await db.work_preferences.find_one({"talent_id": talent_id}) or {}
    skills = [
        doc["skill_name"]
        async for doc in db.talent_skills.find({"talent_id": talent_id})
        if doc.get("skill_name")
    ]
    education = [_serialize(d) async for d in db.educations.find({"talent_id": talent_id})]
    experience = [_serialize(d) async for d in db.experiences.find({"talent_id": talent_id})]

    status, percent = await _compute_completion(db, talent_id, profile, skills)

    return {
        "user_id": talent_id,
        "full_name": current_user.get("full_name", ""),
        "email": current_user["email"],
        "phone": profile.get("phone"),
        "country": profile.get("country"),
        "city": profile.get("city"),
        "headline": profile.get("headline"),
        "dob": profile.get("dob"),
        "gender": profile.get("gender"),
        "cv_filename": profile.get("cv_filename"),
        "cv_uploaded_at": profile.get("cv_uploaded_at"),
        "profile_completion_status": status,
        "profile_completion_percent": percent,
        "skills": skills,
        "preferred_categories": prefs.get("preferred_categories", []),
        "education": education,
        "experience": experience,
        "ai_filled_fields": profile.get("ai_filled_fields", []),
    }


@router.put("/me/profile", response_model=TalentMeOut)
async def update_personal_info(
    payload: PersonalInfoUpdate, current_user: dict = Depends(require_role("talent"))
) -> dict:
    """PUT /talents/me/profile — update personal info (phone, location,
    headline, date of birth, gender)."""
    db = get_database()
    talent_id = current_user["id"]

    # Preserve the difference between omitted fields and fields explicitly
    # cleared by the profile form (sent as null).
    update = payload.model_dump(exclude_unset=True)
    if update:
        # BSON supports datetime but not datetime.date. Store date-only values
        # as ISO strings; Pydantic converts them back to `date` in API output.
        if isinstance(update.get("dob"), date):
            update["dob"] = update["dob"].isoformat()
        await db.talent_profiles.update_one(
            {"user_id": talent_id}, {"$set": {"user_id": talent_id, **update}}, upsert=True
        )
        # A manual edit always wins — drop those fields from ai_filled_fields
        # so the "AI" badge doesn't linger on data the talent just corrected.
        await db.talent_profiles.update_one(
            {"user_id": talent_id},
            {"$pull": {"ai_filled_fields": {"$in": list(update.keys())}}},
        )
    return await get_my_talent_profile(current_user)


@router.put("/me/preferences", response_model=TalentPreferencesOut)
async def update_my_preferences(
    payload: TalentPreferencesUpdate, current_user: dict = Depends(require_role("talent"))
) -> dict:
    """PUT /talents/me/preferences — quick skills/category setup (wholesale
    replace). Exists so job recommendations/matches have real signal to
    work with before full CV-upload/AI-parsing is built."""
    db = get_database()
    talent_id = current_user["id"]

    await db.talent_skills.delete_many({"talent_id": talent_id})
    if payload.skills:
        await db.talent_skills.insert_many(
            [
                {"talent_id": talent_id, "skill_name": s.strip(), "skill_type": "general"}
                for s in payload.skills
                if s.strip()
            ]
        )

    await db.work_preferences.update_one(
        {"talent_id": talent_id},
        {"$set": {"talent_id": talent_id, "preferred_categories": payload.preferred_categories}},
        upsert=True,
    )
    return {"skills": payload.skills, "preferred_categories": payload.preferred_categories}


@router.post("/me/skills", response_model=TalentMeOut, status_code=201)
async def add_skill(
    payload: AddSkillRequest, current_user: dict = Depends(require_role("talent"))
) -> dict:
    """POST /talents/me/skills — add a single skill without touching the
    rest (used by the "suggested skills" quick-add chips)."""
    db = get_database()
    talent_id = current_user["id"]
    name = payload.skill_name.strip()

    existing = await db.talent_skills.find_one({"talent_id": talent_id, "skill_name": name})
    if not existing and name:
        await db.talent_skills.insert_one(
            {"talent_id": talent_id, "skill_name": name, "skill_type": "general"}
        )
    return await get_my_talent_profile(current_user)


@router.delete("/me/skills/{skill_name}", response_model=TalentMeOut)
async def remove_skill(
    skill_name: str, current_user: dict = Depends(require_role("talent"))
) -> dict:
    db = get_database()
    await db.talent_skills.delete_one(
        {"talent_id": current_user["id"], "skill_name": skill_name}
    )
    return await get_my_talent_profile(current_user)


@router.post("/me/education", response_model=EducationOut, status_code=201)
async def add_education(
    payload: EducationEntry, current_user: dict = Depends(require_role("talent"))
) -> dict:
    db = get_database()
    doc = {"talent_id": current_user["id"], "source": "manual", **payload.model_dump()}
    result = await db.educations.insert_one(doc)
    return _serialize({**doc, "_id": result.inserted_id})


@router.delete("/me/education/{education_id}", status_code=204)
async def delete_education(
    education_id: str, current_user: dict = Depends(require_role("talent"))
) -> None:
    db = get_database()
    try:
        oid = ObjectId(education_id)
    except InvalidId:
        raise HTTPException(status_code=404, detail="Not found")
    await db.educations.delete_one({"_id": oid, "talent_id": current_user["id"]})


@router.post("/me/experience", response_model=ExperienceOut, status_code=201)
async def add_experience(
    payload: ExperienceEntry, current_user: dict = Depends(require_role("talent"))
) -> dict:
    db = get_database()
    doc = {"talent_id": current_user["id"], "source": "manual", **payload.model_dump()}
    result = await db.experiences.insert_one(doc)
    return _serialize({**doc, "_id": result.inserted_id})


@router.delete("/me/experience/{experience_id}", status_code=204)
async def delete_experience(
    experience_id: str, current_user: dict = Depends(require_role("talent"))
) -> None:
    db = get_database()
    try:
        oid = ObjectId(experience_id)
    except InvalidId:
        raise HTTPException(status_code=404, detail="Not found")
    await db.experiences.delete_one({"_id": oid, "talent_id": current_user["id"]})


@router.get("/me/saved-jobs", response_model=list[JobOut])
async def my_saved_jobs(current_user: dict = Depends(require_role("talent"))) -> list[dict]:
    """GET /talents/me/saved-jobs — jobs this talent bookmarked, most
    recently saved first, each carrying its persisted AI match score if one
    exists (see POST /jobs/{job_id}/save and GET /matches/me)."""
    db = get_database()
    talent_id = current_user["id"]

    saved_cursor = db.saved_jobs.find({"talent_id": talent_id}).sort("saved_at", -1)
    saved_docs = [doc async for doc in saved_cursor]
    job_ids = [doc["job_id"] for doc in saved_docs]
    if not job_ids:
        return []

    jobs_by_id = {}
    async for job in db.jobs.find({"_id": {"$in": [ObjectId(jid) for jid in job_ids]}}):
        jobs_by_id[str(job["_id"])] = job

    scores = await get_match_scores(db, talent_id, job_ids)

    applied_ids = {
        application["job_id"]
        async for application in db.applications.find(
            {"talent_id": talent_id, "job_id": {"$in": job_ids}}, {"job_id": 1}
        )
    }
    results = []
    for jid in job_ids:
        job = jobs_by_id.get(jid)
        if not job:
            continue
        out = dict(job)
        out["id"] = str(out.pop("_id"))
        out["has_applied"] = jid in applied_ids
        if jid in scores:
            out["match_score"] = scores[jid]
        results.append(out)
    return results


@router.post("/me/cv", response_model=CvUploadResponse)
async def upload_cv(
    current_user: dict = Depends(require_role("talent")),
    file: UploadFile = File(...),
) -> dict:
    """POST /talents/me/cv — upload a CV (PDF/DOCX). Extracts text, asks
    the AI to pull out structured fields, and merges them onto the
    profile.

    Merge rules (this is the part that makes re-uploading safe):
      - Personal info (phone/city/country/headline): only fills a field
        that's currently EMPTY or was itself set by a *previous* CV parse.
        A field the talent typed in manually is never overwritten.
      - Education/experience: previous AI-sourced entries are replaced;
        anything the talent added manually (source="manual") is untouched.
      - Skills: newly found skills are added (case-insensitive dedupe);
        existing skills — manual or AI — are never removed.

    If both AI providers are down, the file still uploads and is stored;
    the talent just fills fields in manually, same as before this endpoint
    existed — CV parsing failing never blocks the upload itself.
    """
    db = get_database()
    talent_id = current_user["id"]

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Please upload a PDF or DOCX file.")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File is too large (5 MB max).")

    try:
        raw_text = extract_text(file.filename or "cv.pdf", content)
    except CvParsingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Store the raw file in GridFS, replacing any previous upload. Best
    # effort — if this fails for any reason, we still proceed with text
    # extraction + AI parsing (the actual point of this endpoint) rather
    # than blocking the whole upload over file storage.
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket

    profile = await db.talent_profiles.find_one({"user_id": talent_id}) or {}
    new_file_id = None
    try:
        bucket = AsyncIOMotorGridFSBucket(db)
        old_file_id = profile.get("cv_file_id")
        if old_file_id:
            try:
                await bucket.delete(ObjectId(old_file_id))
            except Exception:  # noqa: BLE001 — fine if it's already gone
                pass
        new_file_id = await bucket.upload_from_stream(
            file.filename or "cv", content,
            metadata={"talent_id": talent_id, "content_type": file.content_type},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("CV file storage (GridFS) failed, continuing without it: %s", exc)

    extracted, provider = await parse_cv_fields(raw_text)

    now = datetime.now(timezone.utc)
    profile_update = {
        "cv_filename": file.filename,
        "cv_uploaded_at": now.isoformat(),
    }
    if new_file_id is not None:
        profile_update["cv_file_id"] = str(new_file_id)

    fields_updated: list[str] = []
    education_added = 0
    experience_added = 0
    skills_added = 0
    parsed_ok = extracted is not None

    if extracted:
        prior_ai_fields = set(profile.get("ai_filled_fields", []))
        new_ai_fields = list(prior_ai_fields)

        for field in _CV_SCALAR_FIELDS:
            value = extracted.get(field)
            if not value:
                continue
            currently_empty = not profile.get(field)
            was_ai_filled = field in prior_ai_fields
            if currently_empty or was_ai_filled:
                profile_update[field] = value
                fields_updated.append(field)
                if field not in new_ai_fields:
                    new_ai_fields.append(field)

        profile_update["ai_filled_fields"] = new_ai_fields

        if extracted["education"]:
            await db.educations.delete_many({"talent_id": talent_id, "source": "ai_cv"})
            await db.educations.insert_many(
                [{"talent_id": talent_id, "source": "ai_cv", **e} for e in extracted["education"]]
            )
            education_added = len(extracted["education"])

        if extracted["experience"]:
            await db.experiences.delete_many({"talent_id": talent_id, "source": "ai_cv"})
            await db.experiences.insert_many(
                [{"talent_id": talent_id, "source": "ai_cv", **e} for e in extracted["experience"]]
            )
            experience_added = len(extracted["experience"])

        if extracted["skills"]:
            existing_skills = {
                s["skill_name"].lower()
                async for s in db.talent_skills.find({"talent_id": talent_id})
            }
            new_skills = [s for s in extracted["skills"] if s.lower() not in existing_skills]
            if new_skills:
                await db.talent_skills.insert_many(
                    [
                        {"talent_id": talent_id, "skill_name": s, "skill_type": "technical", "source": "ai_cv"}
                        for s in new_skills
                    ]
                )
                skills_added = len(new_skills)

    await db.talent_profiles.update_one(
        {"user_id": talent_id}, {"$set": {"user_id": talent_id, **profile_update}}, upsert=True
    )

    updated_profile = await get_my_talent_profile(current_user)

    if not parsed_ok:
        message = (
            "Your CV was uploaded, but AI parsing isn't available right now "
            "(both Gemini and Groq are unreachable) — please fill in your "
            "profile manually below. Run `python -m scripts.check_ai` on the "
            "backend to diagnose why."
        )
    elif not any([fields_updated, education_added, experience_added, skills_added]):
        message = (
            "Your CV was uploaded and read successfully, but we couldn't "
            "confidently extract new profile fields from it — your existing "
            "info was left untouched. Feel free to fill in anything missing manually."
        )
    else:
        parts = []
        if fields_updated:
            parts.append(f"{len(fields_updated)} personal info field(s)")
        if education_added:
            parts.append(f"{education_added} education entr{'y' if education_added == 1 else 'ies'}")
        if experience_added:
            parts.append(f"{experience_added} experience entr{'y' if experience_added == 1 else 'ies'}")
        if skills_added:
            parts.append(f"{skills_added} skill(s)")
        message = f"CV parsed successfully — filled in {', '.join(parts)}. Review and edit anything below."

    return {
        "profile": updated_profile,
        "parsed_ok": parsed_ok,
        "ai_provider": provider,
        "fields_updated": fields_updated,
        "education_added": education_added,
        "experience_added": experience_added,
        "skills_added": skills_added,
        "message": message,
    }
