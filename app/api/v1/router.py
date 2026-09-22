from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    applications,
    auth,
    courses,
    employers,
    jobs,
    matches,
    messages,
    notifications,
    public,
    talents,
    translations,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(public.router, prefix="/public", tags=["public"])
api_router.include_router(translations.router, prefix="/translations", tags=["translations"])
api_router.include_router(talents.router, prefix="/talents", tags=["talents"])
api_router.include_router(employers.router, prefix="/employers", tags=["employers"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(applications.router, prefix="/applications", tags=["applications"])
api_router.include_router(matches.router, prefix="/matches", tags=["matches"])
api_router.include_router(courses.router, prefix="/courses", tags=["courses"])
api_router.include_router(messages.router, prefix="/messages", tags=["messages"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
