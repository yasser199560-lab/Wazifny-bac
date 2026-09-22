"""Pydantic request/response models for the employer dashboard."""

from datetime import datetime

from pydantic import BaseModel, Field


class CompanyProfileUpdate(BaseModel):
    company_name: str | None = None
    logo_url: str | None = None
    website: str | None = None
    sector: str | None = None
    workforce_size: str | None = None
    lifecycle_stage: str | None = None
    description: str | None = None
    location: str | None = None
    is_hidden: bool | None = None


class CompanyProfileOut(BaseModel):
    user_id: str
    company_name: str
    logo_url: str | None = None
    website: str | None = None
    sector: str | None = None
    workforce_size: str | None = None
    lifecycle_stage: str | None = None
    description: str | None = None
    location: str | None = None
    is_hidden: bool = False


class RecentJobSummary(BaseModel):
    id: str
    title: str
    status: str
    applicants_count: int
    posted_at: datetime | None = None


class TopCandidateSummary(BaseModel):
    talent_id: str
    full_name: str
    headline: str | None = None
    match_score: int | None = None


class EmployerDashboardOut(BaseModel):
    active_jobs: int
    total_applicants: int
    shortlisted: int
    saved_candidates: int
    recent_jobs: list[RecentJobSummary]
    top_candidates: list[TopCandidateSummary]


class CandidateSummary(BaseModel):
    talent_id: str
    full_name: str
    headline: str | None = None
    city: str | None = None
    skills: list[str] = Field(default_factory=list)
    match_score: int | None = None


class SubscriptionPlanOut(BaseModel):
    plan_id: str
    name: str
    price: int
    duration_days: int
    features: list[str]


class SubscriptionOut(BaseModel):
    current_plan: str
    renews_at: datetime | None = None
    price: int
    features: list[str]
    all_plans: list[SubscriptionPlanOut]


class SubscriptionSwitchRequest(BaseModel):
    plan_id: str
