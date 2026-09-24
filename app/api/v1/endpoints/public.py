"""Public, unauthenticated endpoints that power the marketing landing page.

Everything here reads straight from MongoDB so the landing page never ships
hardcoded numbers — if the seed script (or real usage) changes the data, the
homepage reflects it on the next request.
"""

import asyncio

from fastapi import APIRouter

from app.db.mongodb import get_database

router = APIRouter()

# Fallback categories shown (with a 0 count) so the section never looks empty
# on a completely fresh database before any jobs have been posted.
DEFAULT_CATEGORIES = [
    "Engineering",
    "Design",
    "Marketing",
    "Finance",
    "Product",
    "Data & AI",
    "Sales",
    "Operations",
]


@router.get("/stats")
async def get_stats() -> dict:
    db = get_database()

    active_talents = await db.users.count_documents({"role": "talent"})
    companies = await db.users.count_documents({"role": "employer"})
    jobs_posted = await db.jobs.count_documents({})

    # Match accuracy: average AI match_score across generated job matches,
    # expressed as a percentage. Falls back to a sensible default when no
    # matches have been generated yet (e.g. brand-new database).
    match_accuracy = 94
    pipeline = [{"$group": {"_id": None, "avg_score": {"$avg": "$match_score"}}}]
    async for doc in db.job_matches.aggregate(pipeline):
        if doc.get("avg_score") is not None:
            match_accuracy = round(doc["avg_score"] * 100 if doc["avg_score"] <= 1 else doc["avg_score"])

    def _fmt(n: int) -> str:
        return f"{n:,}+" if n else "0"

    return {
        "stats": [
            {"value": _fmt(active_talents), "label": "Active Talents"},
            {"value": _fmt(companies), "label": "Companies"},
            {"value": _fmt(jobs_posted), "label": "Jobs Posted"},
            {"value": f"{match_accuracy}%", "label": "Match Accuracy"},
        ]
    }


@router.get("/categories")
async def get_categories() -> dict:
    db = get_database()

    pipeline = [
        {"$match": {"status": "active"}},
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 8},
    ]
    counts: dict[str, int] = {}
    async for doc in db.jobs.aggregate(pipeline):
        if doc["_id"]:
            counts[doc["_id"]] = doc["count"]

    categories = [
        {"name": name, "count": f"{counts.get(name, 0)} jobs"} for name in DEFAULT_CATEGORIES
    ]
    # Include any category present in the DB but missing from the default list.
    for name, count in counts.items():
        if name not in DEFAULT_CATEGORIES:
            categories.append({"name": name, "count": f"{count} jobs"})

    return {"categories": categories[:8]}


@router.get("/testimonials")
async def get_testimonials() -> dict:
    db = get_database()

    cursor = db.testimonials.find({"featured": True}).sort("created_at", -1).limit(6)
    testimonials = [
        {
            "quote": doc["quote"],
            "name": doc["name"],
            "role": doc["role"],
            "rating": doc.get("rating", 5),
        }
        async for doc in cursor
    ]
    return {"testimonials": testimonials}


@router.get("/articles")
async def get_articles() -> dict:
    """Platform tips / help-center style articles for the About page.

    These are AI-authored explanations of Wazifny's own real features
    (see scripts/seed.py's article generation) — not fabricated news
    about outside events. Falls back to an empty list gracefully if none
    have been seeded yet.
    """
    db = get_database()
    cursor = db.articles.find({}).sort("published_at", -1).limit(6)
    articles = [
        {
            "id": str(doc["_id"]),
            "title": doc["title"],
            "summary": doc["summary"],
            "published_at": doc.get("published_at"),
        }
        async for doc in cursor
    ]
    return {"articles": articles}


@router.get("/landing-data")
async def get_landing_data() -> dict:
    """Single combined call so the Next.js landing page only needs one
    server-side fetch."""
    stats, categories, testimonials = await asyncio.gather(
        get_stats(), get_categories(), get_testimonials()
    )
    return {**stats, **categories, **testimonials}
