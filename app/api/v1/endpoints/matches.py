from fastapi import APIRouter, Depends

from app.db.mongodb import get_database
from app.schemas.job import AiMatchesResponse
from app.services.matching_service import get_ai_matches
from app.utils.deps import require_role

router = APIRouter()


@router.get("/me", response_model=AiMatchesResponse)
async def my_ai_matches(current_user: dict = Depends(require_role("talent"))) -> dict:
    """GET /matches/me — every active job scored 0-100 for this talent,
    best match first (powers the AI Matches page). Scores are persisted to
    `job_matches` so the Applications tracker and Saved Jobs page can reuse
    them without another AI call.

    Reads the same skills/preferred-categories signal as
    `GET /jobs/recommended` (see PUT /talents/me/preferences) — if nothing's
    been set yet, returns `personalized: false` instead of faking a match.
    """
    db = get_database()
    return await get_ai_matches(db, talent_user_id=current_user["id"])
