"""AI translation with deterministic Arabic/English fallbacks.

The UI never depends on an AI provider for its essential wording: if Gemini
and Groq are unavailable, known interface strings use this local dictionary
and unknown content remains readable in its original language.
"""

import json

from app.services.ai_service import ai_complete, extract_json

_MANUAL_AR = {
    "Home": "الرئيسية",
    "Find Jobs": "ابحث عن وظائف",
    "Courses": "الدورات",
    "About Us": "من نحن",
    "Login": "تسجيل الدخول",
    "Register": "إنشاء حساب",
    "Search": "بحث",
    "Location": "الموقع",
    "Category": "الفئة",
    "Find Jobs": "ابحث عن وظائف",
    "Apply Now": "قدّم الآن",
    "Save Job": "حفظ الوظيفة",
    "Job Description": "الوصف الوظيفي",
    "Requirements": "المتطلبات",
    "Company": "الشركة",
    "Salary": "الراتب",
    "Recently posted": "نُشرت مؤخراً",
    "Full-time": "دوام كامل",
    "Part-time": "دوام جزئي",
    "Remote": "عن بُعد",
    "Internship": "تدريب",
    "Contract": "عقد",
}


def manual_translate(text: str, target_language: str) -> str:
    if target_language == "en":
        return text
    return _MANUAL_AR.get(text, text)


async def translate_texts(texts: list[str], target_language: str) -> tuple[list[str], str]:
    """Translate a bounded list while preserving ordering and never failing."""
    if target_language == "en":
        return texts, "manual"

    system_prompt = (
        "You are a precise UI translator. Translate each supplied English text "
        "into natural Modern Standard Arabic. Return JSON only in this exact "
        'shape: {"translations":["..."]}. Preserve URLs, names, numbers, and '
        "the number and order of items."
    )
    raw, provider = await ai_complete(system_prompt, json.dumps({"texts": texts}, ensure_ascii=False))
    parsed = extract_json(raw or "")
    translations = parsed.get("translations") if isinstance(parsed, dict) else None
    if (
        isinstance(translations, list)
        and len(translations) == len(texts)
        and all(isinstance(item, str) and item.strip() for item in translations)
    ):
        return translations, provider or "ai"

    return [manual_translate(text, target_language) for text in texts], "manual"
