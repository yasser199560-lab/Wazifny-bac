"""Mongo document shapes for `talent_profiles`, `educations`, `experiences`,
`talent_skills`, `work_preferences`.

talent_profiles: {
    "_id": ObjectId,
    "user_id": str,
    "dob": date | None,
    "gender": str | None,
    "country": str | None,
    "city": str | None,
    "cv_file_id": str | None,       # GridFS file id
    "profile_completion_status": "incomplete" | "complete",
    "average_rating": float | None,
}
"""
