"""Course catalog + skill-gap analysis.

Design note on the "AI Generated" badge on the Courses page: the gap
*detection* and *severity* are deterministic (real requirement strings from
real active jobs, minus skills the talent already has, scored by what
fraction of candidate jobs need it) — trustworthy, reproducible numbers.
The AI's job is narrower and genuinely suited to a language model: turning
messy raw phrases ("Docker & Kubernetes", "CI/CD pipelines", "Infrastructure
as code") into one clean, human-readable skill label ("Docker / DevOps").
It never invents the counts or severity itself.
"""

from fastapi import APIRouter, Depends

from app.db.mongodb import get_database
from app.schemas.course import CourseOut, SkillGap, SkillGapResponse
from app.services.ai_service import ai_complete, extract_json
from app.utils.deps import require_role

router = APIRouter()

CANDIDATE_JOB_LIMIT = 60
TOP_GAPS = 3


def _serialize_course(doc: dict) -> dict:
    out = dict(doc)
    out["id"] = str(out.pop("_id"))
    return out


def _severity_for(job_count: int, total_jobs: int) -> str:
    if total_jobs == 0:
        return "Low Gap"
    ratio = job_count / total_jobs
    if ratio >= 0.5:
        return "High Gap"
    if ratio >= 0.25:
        return "Medium Gap"
    return "Low Gap"


@router.get("", response_model=list[CourseOut])
async def list_courses() -> list[dict]:
    """GET /courses — browse the full course library."""
    db = get_database()
    cursor = db.courses.find({}).sort("title", 1)
    return [_serialize_course(doc) async for doc in cursor]


@router.get("/skill-gap", response_model=SkillGapResponse)
async def skill_gap(current_user: dict = Depends(require_role("talent"))) -> dict:
    """GET /courses/skill-gap — talent-only. Compares the talent's skills
    against requirements actually listed on real active jobs (in their
    preferred categories if set, else all jobs), and recommends real
    courses from the catalog that address the gaps."""
    db = get_database()
    talent_id = current_user["id"]

    skills = [
        doc["skill_name"].lower()
        async for doc in db.talent_skills.find({"talent_id": talent_id})
        if doc.get("skill_name")
    ]
    prefs = await db.work_preferences.find_one({"talent_id": talent_id}) or {}
    preferred_categories = prefs.get("preferred_categories") or []

    query: dict = {"status": "active"}
    if preferred_categories:
        query["category"] = {"$in": preferred_categories}
    jobs = await db.jobs.find(query).limit(CANDIDATE_JOB_LIMIT).to_list(length=CANDIDATE_JOB_LIMIT)
    total_jobs = len(jobs)

    # Count how many distinct jobs mention each raw requirement phrase,
    # skipping phrases the talent's skills already cover.
    phrase_job_count: dict[str, int] = {}
    for job in jobs:
        seen_in_job = set()
        for req in job.get("requirements", []) or []:
            req_norm = req.strip()
            if not req_norm or req_norm.lower() in seen_in_job:
                continue
            if any(s in req_norm.lower() or req_norm.lower() in s for s in skills):
                continue
            seen_in_job.add(req_norm.lower())
            phrase_job_count[req_norm] = phrase_job_count.get(req_norm, 0) + 1

    if not phrase_job_count:
        courses = [_serialize_course(c) async for c in db.courses.find({}).limit(3)]
        return {"ai_generated": False, "gaps": [], "recommended_courses": courses}

    ranked_phrases = sorted(phrase_job_count.items(), key=lambda kv: kv[1], reverse=True)[:10]

    ai_generated = False
    gap_groups: list[tuple[str, int]] = []

    system_prompt = (
        "You cluster raw job-requirement phrases into a short list of clean, "
        "human-readable skill names. You are given phrases with how many "
        "real jobs require each. Group phrases that refer to the same "
        "underlying skill (e.g. 'Docker & Kubernetes' and 'CI/CD pipelines' "
        "could both belong to 'Docker / DevOps' if genuinely related) and "
        "give each group a short (max 3 word) label. Sum the job counts of "
        "phrases you merge into the same group. Respond with ONLY a JSON "
        f'array of up to {TOP_GAPS} groups, no prose, shaped like: '
        '[{"skill": "<clean label>", "jobs_requiring": <summed count>}], '
        "ordered by jobs_requiring descending. Do not invent counts beyond "
        "what's given — only sum the ones you group together."
    )
    user_prompt = f"Phrases and job counts: {ranked_phrases}"

    raw, _provider = await ai_complete(system_prompt, user_prompt)
    if raw:
        parsed = extract_json(raw)
        if isinstance(parsed, list) and parsed:
            for item in parsed[:TOP_GAPS]:
                if isinstance(item, dict) and item.get("skill"):
                    count = item.get("jobs_requiring")
                    if isinstance(count, int) and count > 0:
                        gap_groups.append((item["skill"], min(count, total_jobs)))
            if gap_groups:
                ai_generated = True

    if not gap_groups:
        # Fallback: raw top phrases, no clustering.
        gap_groups = ranked_phrases[:TOP_GAPS]

    gaps = [
        SkillGap(skill=skill, severity=_severity_for(count, total_jobs), jobs_requiring=count)
        for skill, count in gap_groups
    ]

    # Match real courses by skill_tag against each gap label.
    recommended: list[dict] = []
    seen_ids: set[str] = set()
    for gap in gaps:
        async for course in db.courses.find(
            {"skill_tag": {"$regex": gap.skill.split("/")[0].strip(), "$options": "i"}}
        ).limit(2):
            cid = str(course["_id"])
            if cid not in seen_ids:
                seen_ids.add(cid)
                recommended.append(_serialize_course(course))
    if not recommended:
        recommended = [_serialize_course(c) async for c in db.courses.find({}).limit(3)]

    return {"ai_generated": ai_generated, "gaps": gaps, "recommended_courses": recommended[:3]}
