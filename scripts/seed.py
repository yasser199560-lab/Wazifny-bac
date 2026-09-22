"""Seed the Wazifny MongoDB database with demo data.

Creates (idempotently — safe to run more than once):
  - demo admin, employer (TechCorp Lebanon), a second lightweight employer
    (Fintech Startup, for the messaging demo), and talent (Sara Khalil)
  - 10 sample job postings spanning every landing-page category
  - Sara's skills/preferred categories, persisted AI match scores,
    applications (varied statuses), saved jobs, notifications, and a real
    conversation with TechCorp Lebanon HR
  - the course catalog used by Courses & Skill Gaps
  - featured testimonials
  - AI-authored (Gemini/Groq, with a static fallback) help-center articles
    for the About page — grounded in this project's real features, never
    fabricated "news"

Run from the `backend/` directory, with your virtualenv active and a real
`.env` (MONGODB_URI + AI keys filled in) in place:

    python -m scripts.seed
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.services.ai_service import ai_complete, extract_json  # noqa: E402

DEMO_USERS = [
    {
        "full_name": "Wazifny Admin",
        "email": "admin@wazifny.lb",
        "password": "Admin@12345",
        "role": "admin",
    },
    {
        "full_name": "Karim Rahme",
        "email": "employer@wazifny.lb",
        "password": "Employer@12345",
        "role": "employer",
        "company_name": "TechCorp Lebanon",
    },
    {
        "full_name": "Nadine Aoun",
        "email": "employer2@wazifny.lb",
        "password": "Employer@12345",
        "role": "employer",
        "company_name": "Fintech Startup",
    },
    {
        "full_name": "Sara Khalil",
        "email": "talent@wazifny.lb",
        "password": "Talent@12345",
        "role": "talent",
    },
    # Extra lightweight talent accounts so the employer dashboard
    # (Applicants / Saved Candidates) has more than one real candidate to
    # show — matching the reference design exactly.
    {
        "full_name": "Ahmed Hassan",
        "email": "ahmed.hassan@example.com",
        "password": "Talent@12345",
        "role": "talent",
    },
    {
        "full_name": "Layla Nasr",
        "email": "layla.nasr@example.com",
        "password": "Talent@12345",
        "role": "talent",
    },
    {
        "full_name": "Nour Saad",
        "email": "nour.saad@example.com",
        "password": "Talent@12345",
        "role": "talent",
    },
]

# title, category, location, salary, job_type, application_method, company_name, requirements
JOBS = [
    (
        "Senior Frontend Developer", "Engineering", "Beirut, Lebanon", "$3,000 - $4,500",
        "full_time", "in_platform", "TechCorp Lebanon",
        ["5+ years with React", "Strong TypeScript", "Tailwind CSS", "REST APIs",
         "System Design fundamentals"],
    ),
    (
        "Product Manager", "Product", "Remote - Lebanon", "$2,500 - $3,500",
        "full_time", "external", "Fintech Startup",
        ["Product roadmapping", "Cross-functional leadership", "3+ years PM experience"],
    ),
    (
        "UI/UX Designer", "Design", "Jounieh, Lebanon", "$1,800 - $2,800",
        "full_time", "in_platform", "Creative Agency",
        ["Figma expert", "Design systems", "Portfolio required"],
    ),
    (
        "Data Scientist", "Data & AI", "Beirut, Lebanon", "$3,500 - $5,000",
        "full_time", "in_platform", "Analytics Co.",
        ["Python (pandas, scikit-learn)", "SQL", "Statistics"],
    ),
    (
        "Backend Engineer", "Engineering", "Tripoli, Lebanon", "$2,200 - $3,200",
        "part_time", "external", "CloudBase",
        ["3+ years Python", "GraphQL API experience", "Docker & Kubernetes", "REST API design"],
    ),
    (
        "Marketing Manager", "Marketing", "Beirut, Lebanon", "$2,000 - $3,000",
        "full_time", "in_platform", "GrowthHub",
        ["SEO/SEM", "Social media campaigns", "Content calendars"],
    ),
    (
        "Full Stack Developer", "Engineering", "Beirut, Lebanon", "$2,000 - $3,000",
        "full_time", "in_platform", "DevHouse",
        ["React", "Node.js", "MongoDB", "Docker basics"],
    ),
    (
        "Financial Analyst", "Finance", "Beirut, Lebanon", "$1,100 - $1,600",
        "full_time", "in_platform", "FinLedger",
        ["Financial modeling", "Excel/Google Sheets mastery", "2+ years finance experience"],
    ),
    (
        "Sales Executive", "Sales", "Tripoli, Lebanon", "$800 - $1,300",
        "full_time", "in_platform", "SalesPro Lebanon",
        ["B2B sales experience", "CRM tools", "Strong communication skills"],
    ),
    (
        "Operations Coordinator", "Operations", "Beirut, Lebanon", "$900 - $1,200",
        "full_time", "in_platform", "OpsHub",
        ["Process optimization", "Vendor coordination", "1+ years operations experience"],
    ),
]

# (job title to match, match_score) — mirrors the reference AI Matches design
# exactly, so the demo account reproduces it out of the box.
DEMO_MATCH_SCORES = [
    ("Senior Frontend Developer", 94),
    ("Product Manager", 87),
    ("UI/UX Designer", 81),
    ("Data Scientist", 76),
    ("Backend Engineer", 72),
    ("Marketing Manager", 68),
]

# (job title, status, days ago applied)
DEMO_APPLICATIONS = [
    ("Senior Frontend Developer", "shortlisted", 7),
    ("Product Manager", "reviewed", 9),
    ("UI/UX Designer", "pending", 11),
    ("Full Stack Developer", "rejected", 14),
]

DEMO_SAVED_JOBS = ["Senior Frontend Developer", "UI/UX Designer"]

# Extra lightweight candidates, all applying to the same job (Senior
# Frontend Developer) — matches the reference Applicants/Saved Candidates
# design exactly. (email, skills, headline, city, years_experience, match_score)
EXTRA_CANDIDATES = [
    ("ahmed.hassan@example.com", ["Node.js", "React", "PostgreSQL"], "Full Stack Developer", "Tripoli", 3, 88),
    ("layla.nasr@example.com", ["Figma", "User Research", "Prototyping"], "UI/UX Designer", "Jounieh", 4, 82),
    ("nour.saad@example.com", ["Python", "Django", "AWS"], "Backend Engineer", "Beirut", 6, 79),
]

ANCHOR_JOB_FOR_APPLICANTS = "Senior Frontend Developer"

COURSES = [
    ("Advanced React & TypeScript", "Coursera", "React", 6, 4.8, "https://www.coursera.org/search?query=react%20typescript"),
    ("System Design for Engineers", "Udemy", "System Design", 8, 4.7, "https://www.udemy.com/courses/search/?q=system%20design"),
    ("Data Science with Python", "DataCamp", "Data Science", 10, 4.9, "https://www.datacamp.com/search?q=python"),
    ("Product Management Fundamentals", "edX", "Product", 4, 4.6, "https://www.edx.org/learn/product-management"),
    ("UI/UX Design Bootcamp", "Interaction Design", "Figma", 12, 4.7, "https://www.interaction-design.org/courses"),
    ("Cloud Architecture AWS", "A Cloud Guru", "AWS", 8, 4.5, "https://www.pluralsight.com/cloud-guru"),
    ("Docker & Kubernetes Mastery", "Udemy", "Docker", 6, 4.6, "https://www.udemy.com/courses/search/?q=docker%20kubernetes"),
    ("GraphQL for Frontend Developers", "Coursera", "GraphQL", 3, 4.5, "https://www.coursera.org/search?query=graphql"),
]

TESTIMONIALS = [
    {
        "quote": (
            "I uploaded my CV on a Tuesday and by Thursday I had 3 interview "
            "requests from companies that actually matched my skillset."
        ),
        "name": "Sara Khalil",
        "role": "Frontend Developer, hired via Wazifny",
        "rating": 5,
    },
    {
        "quote": (
            "As an employer, the quality of candidates we receive through "
            "Wazifny is noticeably higher. The AI pre-screening saves us "
            "hours every week."
        ),
        "name": "Karim Rahme",
        "role": "Engineering Manager, TechCorp Lebanon",
        "rating": 5,
    },
    {
        "quote": (
            "The skill-gap analysis pointed me to two courses. I completed "
            "them, updated my profile, and matched to my dream job within a "
            "month."
        ),
        "name": "Layla Nasr",
        "role": "UX Designer, hired remotely",
        "rating": 5,
    },
]

# (title, real facts the article must stick to, static fallback summary)
ARTICLE_TOPICS = [
    (
        "How Job Categories Work on Wazifny",
        "Every job on Wazifny is tagged with exactly one of 8 categories "
        "(Engineering, Design, Marketing, Finance, Product, Data & AI, "
        "Sales, Operations). Categories power the homepage's Browse by "
        "Category section and the Find Jobs filters.",
        "Every job on Wazifny belongs to one of 8 categories — Engineering, "
        "Design, Marketing, Finance, Product, Data & AI, Sales, and "
        "Operations — which is how Browse by Category and job filtering work.",
    ),
    (
        "Understanding Your Profile Completion Score",
        "Profile completion is calculated from personal info fields (city, "
        "phone, headline, date of birth, gender) plus having at least one "
        "education entry, one work experience entry, and one skill. AI job "
        "matching only activates once you've set skills or preferred "
        "categories.",
        "Your profile completion score factors in your personal details, "
        "education, experience, and skills. Filling these in — especially "
        "skills and preferred categories — is what turns on AI job matching.",
    ),
    (
        "How AI Job Matching Works",
        "Wazifny scores every active job from 0-100 against a talent's "
        "skills and preferred categories using Gemini as the primary AI "
        "provider and Groq as an automatic fallback. Jobs scoring 50 or "
        "above show up as Recommended for You. Scores are saved so they "
        "stay consistent across the AI Matches page, Applications tracker, "
        "and Saved Jobs.",
        "Wazifny's AI scores every active job against your skills and "
        "preferred categories, using Gemini with an automatic Groq "
        "fallback so matching never fully stops working. Scores of 50+ "
        "show up as recommended, and the same score follows the job "
        "everywhere you see it.",
    ),
    (
        "In-Platform vs External Applications",
        "Some jobs on Wazifny support one-click in-platform applying, "
        "tracked automatically in your Application Tracker. Others are "
        "marked External and take you to the employer's own careers site "
        "to finish applying there instead.",
        "Jobs marked for in-platform apply let you submit with one click, "
        "tracked right in your Application Tracker. Jobs marked External "
        "send you to the employer's own site to finish applying.",
    ),
    (
        "How Skill-Gap Analysis & Course Recommendations Work",
        "The Courses page compares a talent's current skills against the "
        "real requirements listed on active jobs in their preferred "
        "categories, identifies the most commonly missing skills, and "
        "recommends matching courses from Wazifny's course catalog to help "
        "close those gaps.",
        "Skill-gap analysis compares what you know against what real, "
        "active jobs in your preferred categories actually require, then "
        "points you to courses in the catalog that address the biggest gaps.",
    ),
]


async def upsert_user(db, user: dict) -> str:
    existing = await db.users.find_one({"email": user["email"]})
    if existing:
        return str(existing["_id"])

    doc = {
        "full_name": user["full_name"],
        "email": user["email"],
        "password_hash": hash_password(user["password"]),
        "role": user["role"],
        "phone": None,
        "is_verified": True,
        "is_blocked": False,
        "created_at": datetime.now(timezone.utc),
    }
    result = await db.users.insert_one(doc)
    user_id = str(result.inserted_id)

    if user["role"] == "talent":
        await db.talent_profiles.insert_one(
            {"user_id": user_id, "profile_completion_status": "incomplete"}
        )
    elif user["role"] == "employer":
        await db.employer_profiles.insert_one(
            {"user_id": user_id, "company_name": user["company_name"], "is_hidden": False}
        )
    return user_id


async def seed_jobs(db, employer_id: str) -> dict[str, dict]:
    """Returns {title: job_doc} for every seeded job (inserting only if the
    jobs collection is empty, but always returning the current docs so
    callers can reference their ids)."""
    existing_count = await db.jobs.count_documents({})
    if existing_count == 0:
        now = datetime.now(timezone.utc)
        docs = [
            {
                "employer_id": employer_id,
                "company_name": company_name,
                "title": title,
                "category": category,
                "location": location,
                "salary": salary,
                "job_type": job_type,
                "application_method": application_method,
                "external_url": "https://example.com/careers" if application_method == "external" else None,
                "description": f"{title} role at {company_name}. Join a growing team and make real impact.",
                "requirements": requirements,
                "source": "wazifny",
                "status": "active",
                "posted_at": now - timedelta(days=i),
            }
            for i, (
                title, category, location, salary, job_type, application_method,
                company_name, requirements,
            ) in enumerate(JOBS)
        ]
        await db.jobs.insert_many(docs)
        print(f"  inserted {len(docs)} sample jobs")
    else:
        print(f"  jobs collection already has {existing_count} document(s) — skipping insert")

    return {doc["title"]: doc async for doc in db.jobs.find({})}


async def seed_testimonials(db) -> None:
    existing = await db.testimonials.count_documents({})
    if existing:
        print(f"  testimonials collection already has {existing} document(s) — skipping")
        return
    now = datetime.now(timezone.utc)
    await db.testimonials.insert_many([{**t, "featured": True, "created_at": now} for t in TESTIMONIALS])
    print(f"  inserted {len(TESTIMONIALS)} testimonials")


async def seed_courses(db) -> None:
    existing = await db.courses.count_documents({})
    if existing:
        print(f"  courses collection already has {existing} document(s) — skipping")
        return
    docs = [
        {"title": t, "provider": p, "skill_tag": tag, "duration_weeks": w, "rating": r, "url": u}
        for t, p, tag, w, r, u in COURSES
    ]
    await db.courses.insert_many(docs)
    print(f"  inserted {len(docs)} courses")


async def seed_course_links(db) -> None:
    """Backfill usable provider links for catalog records seeded previously."""
    for title, provider, tag, weeks, rating, url in COURSES:
        await db.courses.update_one(
            {"title": title},
            {"$set": {"provider": provider, "skill_tag": tag, "duration_weeks": weeks, "rating": rating, "url": url}},
        )
    print("  ensured course provider links are available")


async def seed_talent_signal(db, talent_id: str) -> None:
    """Give the demo talent (Sara Khalil) real skills/preferences so AI
    matching/recommendations/skill-gap analysis have something genuine to
    work with out of the box."""
    existing = await db.talent_skills.count_documents({"talent_id": talent_id})
    if existing:
        print("  talent already has skills/preferences set — skipping")
        return

    skills = ["React", "TypeScript", "JavaScript", "Tailwind CSS", "REST APIs"]
    await db.talent_skills.insert_many(
        [{"talent_id": talent_id, "skill_name": s, "skill_type": "technical"} for s in skills]
    )
    await db.work_preferences.update_one(
        {"talent_id": talent_id},
        {"$set": {"talent_id": talent_id, "preferred_categories": ["Engineering", "Design"]}},
        upsert=True,
    )
    await db.talent_profiles.update_one(
        {"user_id": talent_id},
        {
            "$set": {
                "city": "Beirut",
                "country": "Lebanon",
                "phone": "+961 71 234 567",
                "headline": "Frontend Developer",
                "gender": "Female",
            }
        },
        upsert=True,
    )
    await db.educations.insert_one(
        {
            "talent_id": talent_id,
            "degree": "B.Sc. Computer Science",
            "institution": "Lebanese American University",
            "start_date": None,
            "end_date": "2021-01-01",
        }
    )
    await db.experiences.insert_many(
        [
            {
                "talent_id": talent_id,
                "job_title": "Junior Frontend Developer",
                "company_name": "TechStartup LB",
                "start_date": "2021-06-01",
                "end_date": "2023-12-01",
            },
            {
                "talent_id": talent_id,
                "job_title": "Web Developer",
                "company_name": "Freelance",
                "start_date": "2024-01-01",
                "end_date": None,
            },
        ]
    )
    print(f"  seeded skills {skills}, preferred categories, profile basics, education, experience")


async def seed_ai_matches(db, talent_id: str, jobs_by_title: dict[str, dict]) -> None:
    existing = await db.job_matches.count_documents({"talent_id": talent_id})
    if existing:
        print("  job_matches already seeded for talent — skipping")
        return
    now = datetime.now(timezone.utc)
    for title, score in DEMO_MATCH_SCORES:
        job = jobs_by_title.get(title)
        if not job:
            continue
        await db.job_matches.update_one(
            {"talent_id": talent_id, "job_id": str(job["_id"])},
            {"$set": {"talent_id": talent_id, "job_id": str(job["_id"]), "match_score": score, "generated_at": now}},
            upsert=True,
        )
    print(f"  seeded {len(DEMO_MATCH_SCORES)} AI match scores")


async def seed_applications(db, talent_id: str, jobs_by_title: dict[str, dict]) -> None:
    existing = await db.applications.count_documents({"talent_id": talent_id})
    if existing:
        print("  applications already seeded for talent — skipping")
        return
    now = datetime.now(timezone.utc)
    count = 0
    for title, status, days_ago in DEMO_APPLICATIONS:
        job = jobs_by_title.get(title)
        if not job:
            continue
        await db.applications.insert_one(
            {
                "talent_id": talent_id,
                "job_id": str(job["_id"]),
                "applied_via": job.get("application_method", "in_platform"),
                "status": status,
                "applied_at": now - timedelta(days=days_ago),
            }
        )
        count += 1
    print(f"  seeded {count} applications")


async def seed_saved_jobs(db, talent_id: str, jobs_by_title: dict[str, dict]) -> None:
    existing = await db.saved_jobs.count_documents({"talent_id": talent_id})
    if existing:
        print("  saved_jobs already seeded for talent — skipping")
        return
    now = datetime.now(timezone.utc)
    count = 0
    for i, title in enumerate(DEMO_SAVED_JOBS):
        job = jobs_by_title.get(title)
        if not job:
            continue
        await db.saved_jobs.insert_one(
            {"talent_id": talent_id, "job_id": str(job["_id"]), "saved_at": now - timedelta(days=i)}
        )
        count += 1
    print(f"  seeded {count} saved jobs")


async def seed_notifications(db, talent_id: str, jobs_by_title: dict[str, dict]) -> None:
    existing = await db.notifications.count_documents({"user_id": talent_id})
    if existing:
        print("  notifications already seeded for talent — skipping")
        return
    now = datetime.now(timezone.utc)
    items = [
        ("job_match", "New Job Match", "Senior Frontend Developer at TechCorp Lebanon — 94% match", timedelta(hours=2)),
        ("application_update", "Application Update", "TechCorp Lebanon moved your application to Shortlisted", timedelta(hours=5)),
        ("message", "New Message", "TechCorp Lebanon HR sent you a message", timedelta(days=1)),
        ("job_match", "New Job Match", "Backend Engineer at CloudBase — 72% match", timedelta(days=2)),
        ("application_update", "Application Update", "DevHouse has reviewed your application", timedelta(days=3)),
    ]
    docs = [
        {
            "user_id": talent_id,
            "type": t,
            "title": title,
            "content": content,
            "is_read": i >= 2,
            "created_at": now - age,
        }
        for i, (t, title, content, age) in enumerate(items)
    ]
    await db.notifications.insert_many(docs)
    print(f"  seeded {len(docs)} notifications")


async def seed_conversation(db, talent_id: str, employer_id: str) -> None:
    existing = await db.conversations.find_one({"talent_id": talent_id, "employer_id": employer_id})
    if existing:
        print("  demo conversation already exists — skipping")
        return

    now = datetime.now(timezone.utc)
    conv = await db.conversations.insert_one(
        {"talent_id": talent_id, "employer_id": employer_id, "created_at": now, "updated_at": now}
    )
    conv_id = str(conv.inserted_id)
    messages = [
        (employer_id, "Hi Sara, we reviewed your profile and are very impressed!", timedelta(hours=2, minutes=30)),
        (talent_id, "Thank you! I am very interested in the role.", timedelta(hours=2, minutes=25)),
        (employer_id, "We'd like to schedule an interview with you. Are you available this week?", timedelta(hours=2)),
    ]
    await db.messages.insert_many(
        [
            {
                "conversation_id": conv_id,
                "sender_id": sender,
                "content": content,
                "sent_at": now - age,
                "is_read": sender == employer_id,  # the two employer messages are already "read"
            }
            for sender, content, age in messages
        ]
    )
    print("  seeded demo conversation with TechCorp Lebanon HR (3 messages)")


async def generate_article_summary(title: str, facts: str, fallback: str) -> tuple[str, bool]:
    system_prompt = (
        "You write short help-center articles for Wazifny, a real Lebanese "
        "job platform. Write ONLY using the facts given — never invent a "
        "feature, statistic, or claim not in the facts. 2-3 plain-language "
        "sentences, no marketing fluff, no markdown. Output just the "
        "summary text, nothing else."
    )
    user_prompt = f"Topic: {title}\nFacts: {facts}"
    raw, _provider = await ai_complete(system_prompt, user_prompt)
    if raw and len(raw.strip()) > 20:
        return raw.strip(), True
    return fallback, False


async def seed_articles(db) -> None:
    existing = await db.articles.count_documents({})
    if existing:
        print(f"  articles collection already has {existing} document(s) — skipping")
        return

    now = datetime.now(timezone.utc)
    ai_count = 0
    for i, (title, facts, fallback) in enumerate(ARTICLE_TOPICS):
        summary, was_ai = await generate_article_summary(title, facts, fallback)
        ai_count += int(was_ai)
        await db.articles.insert_one(
            {
                "title": title,
                "summary": summary,
                "ai_generated": was_ai,
                "published_at": now - timedelta(days=i),
            }
        )
    print(f"  inserted {len(ARTICLE_TOPICS)} articles ({ai_count} AI-generated, "
          f"{len(ARTICLE_TOPICS) - ai_count} used the static fallback)")


async def seed_extra_candidates(db, ids: dict[str, str], employer_id: str, jobs_by_title: dict[str, dict]) -> None:
    """Seeds 3 more talents (Ahmed, Layla, Nour), each with skills, an
    application + persisted match score against the anchor job, and a
    saved-candidate bookmark by the demo employer — this is what makes
    Applicants / Saved Candidates show more than just Sara."""
    job = jobs_by_title.get(ANCHOR_JOB_FOR_APPLICANTS)
    if not job:
        return
    job_id = str(job["_id"])

    already = await db.applications.count_documents({"job_id": job_id})
    if already >= 1 + len(EXTRA_CANDIDATES):
        print("  extra candidates already seeded — skipping")
        return

    now = datetime.now(timezone.utc)
    for i, (email, skills, headline, city, years, score) in enumerate(EXTRA_CANDIDATES):
        talent_id = ids[email]

        if not await db.talent_skills.count_documents({"talent_id": talent_id}):
            await db.talent_skills.insert_many(
                [{"talent_id": talent_id, "skill_name": s, "skill_type": "technical"} for s in skills]
            )
        await db.talent_profiles.update_one(
            {"user_id": talent_id},
            {"$set": {"headline": headline, "city": city, "country": "Lebanon"}},
            upsert=True,
        )
        if years:
            await db.experiences.update_one(
                {"talent_id": talent_id, "job_title": headline},
                {"$set": {
                    "talent_id": talent_id, "job_title": headline, "company_name": "Previous Company",
                    "start_date": str(2026 - years), "end_date": None,
                }},
                upsert=True,
            )

        await db.job_matches.update_one(
            {"talent_id": talent_id, "job_id": job_id},
            {"$set": {"talent_id": talent_id, "job_id": job_id, "match_score": score, "generated_at": now}},
            upsert=True,
        )

        if not await db.applications.find_one({"talent_id": talent_id, "job_id": job_id}):
            await db.applications.insert_one(
                {
                    "talent_id": talent_id, "job_id": job_id,
                    "applied_via": job.get("application_method", "in_platform"),
                    "status": "pending", "applied_at": now - timedelta(hours=i + 1),
                }
            )

        await db.saved_candidates.update_one(
            {"employer_id": employer_id, "talent_id": talent_id},
            {"$setOnInsert": {"employer_id": employer_id, "talent_id": talent_id, "saved_at": now - timedelta(days=i)}},
            upsert=True,
        )
    print(f"  seeded {len(EXTRA_CANDIDATES)} extra candidates (skills, match scores, applications, saved)")


async def seed_company_profile(db, employer_id: str) -> None:
    profile = await db.employer_profiles.find_one({"user_id": employer_id}) or {}
    if profile.get("website"):
        print("  company profile already enriched — skipping")
        return
    await db.employer_profiles.update_one(
        {"user_id": employer_id},
        {"$set": {
            "website": "https://techcorp.lb",
            "sector": "Technology",
            "workforce_size": "51-200",
            "lifecycle_stage": "Growth",
            "location": "Beirut, Lebanon",
            "description": (
                "TechCorp Lebanon is a leading software development company "
                "providing enterprise solutions across the MENA region."
            ),
            "is_hidden": False,
        }},
    )
    print("  enriched TechCorp Lebanon's company profile")


async def seed_subscription(db, employer_id: str) -> None:
    existing = await db.employer_subscriptions.find_one({"employer_id": employer_id})
    if existing:
        print("  subscription already seeded — skipping")
        return
    now = datetime.now(timezone.utc)
    await db.employer_subscriptions.insert_one(
        {
            "employer_id": employer_id,
            "plan_id": "growth",
            "start_date": now - timedelta(days=23),
            "end_date": now + timedelta(days=7),
            "status": "active",
        }
    )
    print("  seeded Growth subscription (renews in 7 days)")


async def seed_employer_notifications(db, employer_id: str) -> None:
    existing = await db.notifications.count_documents({"user_id": employer_id})
    if existing:
        print("  employer notifications already seeded — skipping")
        return
    now = datetime.now(timezone.utc)
    items = [
        ("application", "New Application Received", "Sara Khalil applied to Senior Frontend Developer — 94% match", timedelta(hours=1)),
        ("application", "New Application Received", "Ahmed Hassan applied to Senior Frontend Developer — 88% match", timedelta(hours=3)),
        ("message", "New Message", "Layla Nasr replied to your interview invitation", timedelta(days=1)),
        ("subscription", "Subscription Reminder", "Your Growth plan renews in 7 days", timedelta(days=2)),
        ("application", "New Application Received", "Nour Saad applied to Senior Frontend Developer — 79% match", timedelta(days=3)),
    ]
    docs = [
        {"user_id": employer_id, "type": t, "title": title, "content": content, "is_read": i >= 2, "created_at": now - age}
        for i, (t, title, content, age) in enumerate(items)
    ]
    await db.notifications.insert_many(docs)
    print(f"  seeded {len(docs)} employer notifications")


async def seed_admin_demo_state(db) -> None:
    """Provide safe, idempotent moderation examples for the admin screens."""
    layla = await db.users.find_one({"email": "layla.nasr@example.com"})
    if layla:
        await db.users.update_one(
            {"_id": layla["_id"]},
            {"$set": {"is_blocked": True, "blocked_reason": "Spam activity"}},
        )
    await db.jobs.update_one(
        {"title": "Product Manager"}, {"$set": {"status": "pending"}}
    )
    print("  seeded admin moderation examples (one blocked account, one pending job)")


async def main() -> None:
    if settings.environment.lower() == "production":
        raise RuntimeError("Refusing to seed demo accounts in production")

    print(f"Connecting to {settings.mongodb_db_name!r} database...")
    client = AsyncIOMotorClient(settings.mongodb_uri)
    db = client[settings.mongodb_db_name]

    ids: dict[str, str] = {}
    print("Seeding demo users...")
    for user in DEMO_USERS:
        user_id = await upsert_user(db, user)
        key = user["email"]
        ids[key] = user_id
        print(f"  {user['role']:<9} {user['email']:<24} -> id {user_id}")

    print("Seeding sample jobs...")
    jobs_by_title = await seed_jobs(db, ids["employer@wazifny.lb"])

    print("Seeding testimonials...")
    await seed_testimonials(db)

    print("Seeding course catalog...")
    await seed_courses(db)
    await seed_course_links(db)

    talent_id = ids["talent@wazifny.lb"]
    employer_id = ids["employer@wazifny.lb"]

    print("Seeding demo talent's profile, skills & preferences...")
    await seed_talent_signal(db, talent_id)

    print("Seeding AI match scores (AI Matches page)...")
    await seed_ai_matches(db, talent_id, jobs_by_title)

    print("Seeding applications (Application Tracker)...")
    await seed_applications(db, talent_id, jobs_by_title)

    print("Seeding saved jobs...")
    await seed_saved_jobs(db, talent_id, jobs_by_title)

    print("Seeding notifications...")
    await seed_notifications(db, talent_id, jobs_by_title)

    print("Seeding demo conversation (Messages)...")
    await seed_conversation(db, talent_id, employer_id)

    print("Seeding extra candidates (Applicants / Saved Candidates)...")
    await seed_extra_candidates(db, ids, employer_id, jobs_by_title)

    print("Seeding company profile...")
    await seed_company_profile(db, employer_id)

    print("Seeding subscription...")
    await seed_subscription(db, employer_id)

    print("Seeding employer notifications...")
    await seed_employer_notifications(db, employer_id)

    print("Seeding admin moderation examples...")
    await seed_admin_demo_state(db)

    print("Seeding About page articles (AI-authored, grounded in real features)...")
    await seed_articles(db)

    client.close()

    print("\nDone. Demo credentials:")
    print("-" * 66)
    for user in DEMO_USERS:
        print(f"  {user['role']:<9} email: {user['email']:<28} password: {user['password']}")
    print("-" * 66)
    print("(Ahmed/Layla/Nour are lightweight extra candidates for the employer")
    print(" dashboard demo — same password as Sara, Talent@12345.)")


if __name__ == "__main__":
    asyncio.run(main())
