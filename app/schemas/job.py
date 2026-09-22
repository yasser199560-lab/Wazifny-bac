"""Pydantic request/response models for job posting, search, and filters."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class JobCreate(BaseModel):
    title: str = Field(..., min_length=2)
    category: str
    location: str
    salary: str | None = None
    job_type: Literal["full_time", "part_time", "internship", "remote", "contract"] = "full_time"
    application_method: Literal["in_platform", "external"] = "in_platform"
    external_url: str | None = None
    description: str = ""
    requirements: list[str] = Field(default_factory=list)


class JobOut(BaseModel):
    id: str
    employer_id: str
    title: str
    category: str
    location: str
    salary: str | None = None
    job_type: str
    application_method: str
    external_url: str | None = None
    description: str = ""
    requirements: list[str] = Field(default_factory=list)
    status: str = "active"
    posted_at: datetime | None = None
    company_name: str | None = None
    company_logo_url: str | None = None
    source: str = "wazifny"
    match_reason: str | None = None
    match_score: int | None = None
    applicants_count: int | None = None
    has_applied: bool = False


class JobSearchResponse(BaseModel):
    jobs: list[JobOut]
    ai_ranked: bool = False
    ai_provider: str | None = None


class RecommendedJobsResponse(BaseModel):
    personalized: bool
    ai_ranked: bool = False
    ai_provider: str | None = None
    recommended: list[JobOut]
    other: list[JobOut]


class AiMatchesResponse(BaseModel):
    personalized: bool
    ai_ranked: bool = False
    matches: list[JobOut]
