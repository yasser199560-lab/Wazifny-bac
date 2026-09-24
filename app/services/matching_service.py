"""Job matching: AI-assisted search ranking + per-talent scoring.

Three entry points, all built on one shared scoring core
(`_score_jobs_for_talent`) so "AI Matches" and "Recommended for you" never
disagree with each other:

  - `search_jobs(...)` — free-text search ranking (landing page + Find Jobs).
  - `get_ai_matches(...)` — the dedicated AI Matches page. Scores every
    active job 0-100 for a talent and persists the result into
    `job_matches` (per the ERD: match_id, talent_id, job_id, match_score,
    generated_at) so match scores are stable/reusable elsewhere (the
    Applications tracker and Saved Jobs page both display "% match" by
    reading this same collection instead of recomputing).
  - `get_recommended_jobs(...)` — the Find Jobs page's "Recommended for
    you" vs "More opportunities" split, using the same scores.

Every AI step has a plain-keyword/category fallback so results are never
just empty because Gemini and Groq are both having a bad day. Nothing here
ever fabricates a job — it only scores/reorders/filters real documents
already in MongoDB.
"""

import re
from datetime import datetime, timezone

from app.services.ai_service import ai_complete, extract_json

CANDIDATE_POOL_LIMIT = 60
RECOMMENDED_THRESHOLD = 50


def _serialize(doc: dict, reason: str | None = None, score: int | None = None) -> dict:
    out = dict(doc)
    out["id"] = str(out.pop("_id"))
    if reason:
        out["match_reason"] = reason
    if score is not None:
        out["match_score"] = score
    return out


async def attach_current_company_profiles(db, jobs: list[dict]) -> list[dict]:
    """Use current company branding instead of the job's creation-time snapshot."""
    employer_ids = list({job.get("employer_id") for job in jobs if job.get("employer_id")})
    if not employer_ids:
        return jobs

    profiles = {
        profile["user_id"]: profile
        async for profile in db.employer_profiles.find(
            {"user_id": {"$in": employer_ids}},
            {"user_id": 1, "company_name": 1, "logo_url": 1, "logo_file_id": 1},
        )
    }
    for job in jobs:
        profile = profiles.get(job.get("employer_id"))
        if not profile:
            continue
        job["company_name"] = profile.get("company_name") or job.get("company_name")
        if profile.get("logo_file_id"):
            job["company_logo_url"] = profile.get("logo_url") or (
                f"/api/v1/employers/{profile['user_id']}/media/logo"
            )
        else:
            job["company_logo_url"] = profile.get("logo_url")
    return jobs


async def mark_applied_jobs(db, talent_id: str, jobs: list[dict]) -> list[dict]:
    """Attach the current talent's application state without exposing it publicly."""
    if not jobs:
        return jobs
    job_ids = [job["id"] for job in jobs]
    applied_ids = {
        application["job_id"]
        async for application in db.applications.find(
            {"talent_id": talent_id, "job_id": {"$in": job_ids}}, {"job_id": 1}
        )
    }
    for job in jobs:
        job["has_applied"] = job["id"] in applied_ids
    return jobs


def _keyword_score(text_query: str, job: dict) -> int:
    terms = [t for t in re.split(r"\W+", text_query.lower()) if len(t) > 1]
    haystack = " ".join(
        [
            job.get("title", ""),
            job.get("category", ""),
            job.get("location", ""),
            job.get("description", ""),
        ]
    ).lower()
    return sum(haystack.count(term) for term in terms)


def _compact(jobs: list[dict]) -> list[dict]:
    """Small, token-cheap representation of each job for the LLM prompt."""
    return [
        {
            "id": str(j["_id"]),
            "title": j.get("title", ""),
            "category": j.get("category", ""),
            "location": j.get("location", ""),
            "job_type": j.get("job_type", ""),
            "summary": (j.get("description") or "")[:220],
        }
        for j in jobs
    ]


async def _build_candidate_pool(
    db, category: str | None, location: str | None, job_type: str | None
) -> list[dict]:
    query: dict = {"status": "active"}
    if category:
        query["category"] = category
    if location:
        query["location"] = {"$regex": re.escape(location), "$options": "i"}
    if job_type:
        query["job_type"] = job_type

    cursor = db.jobs.find(query).sort("posted_at", -1).limit(CANDIDATE_POOL_LIMIT)
    return [doc async for doc in cursor]


async def search_jobs(
    db,
    q: str | None,
    category: str | None,
    location: str | None,
    job_type: str | None,
    limit: int = 30,
) -> dict:
    """Returns {"jobs": [...], "ai_ranked": bool}."""
    candidates = await _build_candidate_pool(db, category, location, job_type)

    if not q or not q.strip():
        jobs = await attach_current_company_profiles(
            db, [_serialize(j) for j in candidates[:limit]]
        )
        return {"jobs": jobs, "ai_ranked": False}

    q = q.strip()

    system_prompt = (
        "You are the search-relevance engine for Wazifny, a Lebanese job "
        "board. You are given a job seeker's free-text search query and a "
        "JSON list of REAL job postings already in the database. Your only "
        "job is to decide which of these listed jobs are actually relevant "
        "to the query, and order them best-match first. Never invent a job "
        "that isn't in the list. Consider synonyms and related skills (e.g. "
        "'React dev' should match a 'Frontend Developer' posting that "
        "mentions React). Respond with ONLY a JSON array, no prose, no "
        'markdown fences, shaped like: [{"id": "<job id>", "reason": '
        '"<max 12 words on why it matches>"}, ...] ordered best-first. '
        "Omit jobs that are not a reasonable match. If nothing matches "
        "well, return []."
    )
    user_prompt = f'Search query: "{q}"\n\nJobs (JSON): {_compact(candidates)}'

    raw, provider = await ai_complete(system_prompt, user_prompt)
    if raw:
        parsed = extract_json(raw)
        if isinstance(parsed, list):
            by_id = {str(j["_id"]): j for j in candidates}
            ranked = []
            for item in parsed:
                job_id = item.get("id") if isinstance(item, dict) else None
                if job_id in by_id:
                    reason = item.get("reason") if isinstance(item, dict) else None
                    ranked.append(_serialize(by_id[job_id], reason))
            if ranked:
                ranked = await attach_current_company_profiles(db, ranked[:limit])
                return {"jobs": ranked, "ai_ranked": True, "ai_provider": provider}

    # Fallback: plain keyword scoring — never leave the user with a blank page.
    scored = [(job, _keyword_score(q, job)) for job in candidates]
    scored = [pair for pair in scored if pair[1] > 0] or [(j, 0) for j in candidates]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    jobs = await attach_current_company_profiles(
        db, [_serialize(job) for job, _score in scored[:limit]]
    )
    return {"jobs": jobs, "ai_ranked": False}


async def _get_talent_signal(db, talent_id: str) -> tuple[list[str], list[str]]:
    skills = [
        doc["skill_name"]
        async for doc in db.talent_skills.find({"talent_id": talent_id})
        if doc.get("skill_name")
    ]
    prefs = await db.work_preferences.find_one({"talent_id": talent_id})
    preferred_categories = (prefs or {}).get("preferred_categories") or []
    return skills, preferred_categories


async def _score_jobs_for_talent(
    db, talent_id: str
) -> tuple[list[tuple[dict, int, str | None]], bool, bool]:
    """Core scoring pass shared by AI Matches + Recommended for you.

    Returns (scored_jobs, personalized, ai_ranked) where scored_jobs is a
    list of (job_doc, score 0-100, reason) sorted best-first.
    """
    skills, preferred_categories = await _get_talent_signal(db, talent_id)
    all_jobs = await db.jobs.find({"status": "active"}).sort("posted_at", -1).limit(
        CANDIDATE_POOL_LIMIT
    ).to_list(length=CANDIDATE_POOL_LIMIT)

    if not skills and not preferred_categories:
        return [], False, False

    system_prompt = (
        "You are the job-matching engine for Wazifny, a Lebanese job board. "
        "You are given a talent's skills / preferred job categories, and a "
        "JSON list of REAL active job postings. Score how well each job "
        "fits this talent from 0 (no fit) to 100 (excellent fit), using "
        "the skills/categories plus any overlap in the job's title/summary. "
        "Respond with ONLY a JSON array, no prose, shaped like: "
        '[{"id": "<job id>", "score": <0-100>, "reason": "<max 12 words>"}, '
        "...] for every job in the list, in any order."
    )
    user_prompt = (
        f"Talent skills: {skills or '(none specified)'}\n"
        f"Talent preferred categories: {preferred_categories or '(none specified)'}\n\n"
        f"Jobs (JSON): {_compact(all_jobs)}"
    )

    raw, provider = await ai_complete(system_prompt, user_prompt)
    if raw:
        parsed = extract_json(raw)
        if isinstance(parsed, list) and parsed:
            by_id = {str(j["_id"]): j for j in all_jobs}
            scored = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                job_id = item.get("id")
                if job_id not in by_id:
                    continue
                scored.append((by_id[job_id], int(item.get("score", 0)), item.get("reason")))
            if scored:
                scored.sort(key=lambda t: t[1], reverse=True)
                return scored, True, True

    # Fallback: category-match gets a flat score, everything else 0.
    scored = [
        (job, 60 if job.get("category") in preferred_categories else 20, None)
        for job in all_jobs
    ]
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored, True, False


async def get_ai_matches(db, talent_user_id: str, limit: int = 20) -> dict:
    """Returns {"personalized": bool, "ai_ranked": bool, "matches": [...]}.

    Also persists each score into `job_matches` (per the ERD) so other
    pages (Applications tracker, Saved Jobs) can display the same "% match"
    without recomputing it.
    """
    scored, personalized, ai_ranked = await _score_jobs_for_talent(db, talent_user_id)
    if not personalized:
        return {"personalized": False, "ai_ranked": False, "matches": []}

    top = scored[:limit]
    now = datetime.now(timezone.utc)
    for job, score, _reason in top:
        await db.job_matches.update_one(
            {"talent_id": talent_user_id, "job_id": str(job["_id"])},
            {
                "$set": {
                    "talent_id": talent_user_id,
                    "job_id": str(job["_id"]),
                    "match_score": score,
                    "generated_at": now,
                }
            },
            upsert=True,
        )

    matches = await mark_applied_jobs(
        db, talent_user_id, [_serialize(job, reason, score) for job, score, reason in top]
    )
    matches = await attach_current_company_profiles(db, matches)
    return {"personalized": True, "ai_ranked": ai_ranked, "matches": matches}


async def get_recommended_jobs(db, talent_user_id: str, limit: int = 6) -> dict:
    """Returns {"personalized": bool, "ai_ranked": bool, "recommended": [...], "other": [...]}."""
    scored, personalized, ai_ranked = await _score_jobs_for_talent(db, talent_user_id)

    if not personalized:
        all_jobs = await db.jobs.find({"status": "active"}).sort("posted_at", -1).limit(
            limit * 3
        ).to_list(length=limit * 3)
        other = await mark_applied_jobs(db, talent_user_id, [_serialize(j) for j in all_jobs])
        other = await attach_current_company_profiles(db, other)
        return {
            "personalized": False,
            "ai_ranked": False,
            "recommended": [],
            "other": other,
        }

    recommended = [
        _serialize(job, reason, score)
        for job, score, reason in scored
        if score >= RECOMMENDED_THRESHOLD
    ][:limit]
    recommended_ids = {j["id"] for j in recommended}
    other = [
        _serialize(job, None, score)
        for job, score, _reason in scored
        if str(job["_id"]) not in recommended_ids
    ]
    await mark_applied_jobs(db, talent_user_id, recommended)
    await mark_applied_jobs(db, talent_user_id, other)
    recommended = await attach_current_company_profiles(db, recommended)
    other = await attach_current_company_profiles(db, other)
    return {
        "personalized": True,
        "ai_ranked": ai_ranked,
        "recommended": recommended,
        "other": other,
    }


async def get_match_scores(db, talent_id: str, job_ids: list[str]) -> dict[str, int]:
    """Bulk-read already-persisted match scores (job_matches) for a set of
    job ids — used by Applications/Saved Jobs so they don't need to call
    the AI again just to show a "% match" badge."""
    if not job_ids:
        return {}
    cursor = db.job_matches.find({"talent_id": talent_id, "job_id": {"$in": job_ids}})
    return {doc["job_id"]: doc["match_score"] async for doc in cursor}
