from pydantic import BaseModel


class CourseOut(BaseModel):
    id: str
    title: str
    provider: str
    skill_tag: str
    duration_weeks: int
    rating: float
    url: str | None = None


class SkillGap(BaseModel):
    skill: str
    severity: str  # "High Gap" | "Medium Gap" | "Low Gap"
    jobs_requiring: int


class SkillGapResponse(BaseModel):
    ai_generated: bool
    gaps: list[SkillGap]
    recommended_courses: list[CourseOut]
