"""Mongo document shape for the `jobs` collection.

{
    "_id": ObjectId,
    "employer_id": str,
    "company_name": str | None,
    "title": str,
    "category": str,
    "location": str,
    "salary": str | None,
    "job_type": "full_time" | "part_time" | "internship" | "remote" | "contract",
    "application_method": "in_platform" | "external",
    "external_url": str | None,
    "description": str,
    "source": "wazifny" | "external",
    "status": "active" | "closed" | "pending_review",
    "posted_at": datetime,
}
"""
