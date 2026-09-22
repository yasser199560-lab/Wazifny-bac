"""Pydantic request/response models for talent profile + preferences."""

from datetime import date

from pydantic import BaseModel, Field


class TalentPreferencesUpdate(BaseModel):
    """Lightweight "quick setup" a talent can fill in immediately after
    registering, so job recommendations have *something* to work with
    even before uploading a CV.
    """

    skills: list[str] = Field(default_factory=list, max_length=30)
    preferred_categories: list[str] = Field(default_factory=list, max_length=10)


class TalentPreferencesOut(BaseModel):
    skills: list[str] = Field(default_factory=list)
    preferred_categories: list[str] = Field(default_factory=list)


class AddSkillRequest(BaseModel):
    skill_name: str = Field(..., min_length=1, max_length=60)


class PersonalInfoUpdate(BaseModel):
    phone: str | None = None
    country: str | None = None
    city: str | None = None
    headline: str | None = Field(None, max_length=100, description="e.g. 'Frontend Developer'")
    dob: date | None = None
    gender: str | None = None


class EducationEntry(BaseModel):
    degree: str
    institution: str
    # Free text, not a strict date — real CVs write "2019", "Jun 2021 - Present",
    # "Expected 2027", etc., and forcing ISO dates would reject most of them.
    start_date: str | None = None
    end_date: str | None = None


class ExperienceEntry(BaseModel):
    job_title: str
    company_name: str
    start_date: str | None = None
    end_date: str | None = None


class EducationOut(EducationEntry):
    id: str
    source: str = "manual"  # "manual" | "ai_cv"


class ExperienceOut(ExperienceEntry):
    id: str
    source: str = "manual"


class TalentMeOut(BaseModel):
    user_id: str
    full_name: str
    email: str
    phone: str | None = None
    country: str | None = None
    city: str | None = None
    headline: str | None = None
    dob: date | None = None
    gender: str | None = None
    cv_filename: str | None = None
    cv_uploaded_at: str | None = None
    profile_completion_status: str = "incomplete"
    profile_completion_percent: int = 0
    skills: list[str] = Field(default_factory=list)
    preferred_categories: list[str] = Field(default_factory=list)
    education: list[EducationOut] = Field(default_factory=list)
    experience: list[ExperienceOut] = Field(default_factory=list)
    # Scalar personal-info fields the most recent CV parse filled in —
    # lets the frontend show an "AI" badge next to them.
    ai_filled_fields: list[str] = Field(default_factory=list)


class CvUploadResponse(BaseModel):
    profile: TalentMeOut
    parsed_ok: bool
    ai_provider: str | None = None
    fields_updated: list[str] = Field(default_factory=list)
    education_added: int = 0
    experience_added: int = 0
    skills_added: int = 0
    message: str
