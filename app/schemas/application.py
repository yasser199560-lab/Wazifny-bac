"""Pydantic request/response models for applications."""

from datetime import datetime

from pydantic import BaseModel


class ApplicationCreate(BaseModel):
    job_id: str


class ApplicationStatusUpdate(BaseModel):
    status: str  # "pending" | "reviewed" | "shortlisted" | "rejected" | "hired"


class ApplicationOut(BaseModel):
    id: str
    job_id: str
    talent_id: str
    applied_via: str
    status: str = "pending"  # "pending" | "reviewed" | "shortlisted" | "rejected" | "hired"
    applied_at: datetime | None = None
    job_title: str | None = None
    company_name: str | None = None
    employer_id: str | None = None
    match_score: int | None = None


class AiScreeningResult(BaseModel):
    ai_available: bool
    ai_provider: str | None = None
    recommendation: str | None = None  # "shortlist" | "consider" | "reject"
    reasoning: str | None = None
    strengths: list[str] = []
    gaps: list[str] = []
