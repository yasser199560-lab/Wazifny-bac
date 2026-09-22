"""Mongo document shapes for `employer_profiles`, `subscription_plans`,
`employer_subscriptions`.

employer_profiles: {
    "_id": ObjectId,
    "user_id": str,
    "company_name": str,
    "sector": str | None,
    "workforce_size": str | None,
    "lifecycle_stage": str | None,
    "is_hidden": bool,
}
"""
