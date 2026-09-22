"""CV parsing pipeline (BRD §4.1 — CV Parsing & Profile Autofill):

1. Extract raw text from the uploaded PDF/DOCX (`extract_text`, using
   pdfplumber / python-docx per the tech stack).
2. Ask the AI (Gemini primary, Groq fallback — see ai_service.py) to turn
   that text into structured profile JSON: personal info, education,
   experience, skills.
3. talents.py merges the result onto the talent's profile, *never*
   overwriting a field the talent already set manually (see the `source`
   tracking there) — missing-field detection is just "what's still empty
   after this merge", surfaced to the frontend via TalentMeOut.

The AI is only ever asked to extract facts already present in the
uploaded text — it's told explicitly not to invent anything, and every
field it can't find confidently comes back null rather than guessed.
"""

import io
import logging

from app.services.ai_service import ai_complete, extract_json

logger = logging.getLogger("wazifny.cv")

MAX_CV_TEXT_CHARS = 12000  # keep the AI prompt a reasonable size
ALLOWED_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


class CvParsingError(Exception):
    pass


def extract_text(filename: str, content: bytes) -> str:
    """Extracts raw text from a PDF or DOCX file's bytes."""
    lower = filename.lower()

    if lower.endswith(".pdf"):
        import pdfplumber

        text_parts = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        text = "\n".join(text_parts)

    elif lower.endswith(".docx"):
        import docx

        document = docx.Document(io.BytesIO(content))
        text = "\n".join(p.text for p in document.paragraphs if p.text)

    else:
        raise CvParsingError("Unsupported file type — please upload a PDF or DOCX.")

    text = text.strip()
    if not text:
        raise CvParsingError(
            "Couldn't read any text from this file — it may be a scanned "
            "image rather than a text-based document."
        )
    return text[:MAX_CV_TEXT_CHARS]


async def parse_cv_fields(raw_text: str) -> tuple[dict | None, str | None]:
    """Returns (extracted_fields, ai_provider). extracted_fields is None if
    both AI providers failed — caller should still save the raw CV and let
    the talent fill everything in manually rather than blocking the upload."""

    system_prompt = (
        "You extract structured resume data from raw CV/resume text for "
        "Wazifny, a job platform. Use ONLY information explicitly present "
        "in the text — never invent, guess, or infer anything not stated. "
        "If a field isn't clearly present, use null (for strings) or an "
        "empty array (for lists) rather than guessing. Dates can be "
        "approximate free text exactly as written in the CV (e.g. '2021', "
        "'Jun 2021', 'Present') — do not reformat them. Respond with ONLY "
        "a JSON object, no prose, no markdown fences, in this exact shape:\n"
        '{"phone": "<string or null>", "city": "<string or null>", '
        '"country": "<string or null>", "headline": "<current/most recent '
        'job title, or null>", '
        '"education": [{"degree": "<string>", "institution": "<string>", '
        '"start_date": "<string or null>", "end_date": "<string or null>"}], '
        '"experience": [{"job_title": "<string>", "company_name": "<string>", '
        '"start_date": "<string or null>", "end_date": "<string or null>"}], '
        '"skills": ["<string>", ...]}'
    )
    user_prompt = f"CV text:\n\n{raw_text}"

    raw, provider = await ai_complete(system_prompt, user_prompt)
    if not raw:
        return None, None

    parsed = extract_json(raw)
    if not isinstance(parsed, dict):
        logger.warning("CV parse: AI response wasn't a JSON object, discarding.")
        return None, provider

    # Defensive shape-normalization — never trust the model's output blindly.
    def _str_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]

    def _entries(value, keys: tuple[str, str]) -> list[dict]:
        out = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get(keys[0]) and item.get(keys[1]):
                    out.append(
                        {
                            keys[0]: str(item[keys[0]]).strip(),
                            keys[1]: str(item[keys[1]]).strip(),
                            "start_date": (str(item["start_date"]).strip() if item.get("start_date") else None),
                            "end_date": (str(item["end_date"]).strip() if item.get("end_date") else None),
                        }
                    )
        return out

    normalized = {
        "phone": (str(parsed.get("phone")).strip() if parsed.get("phone") else None),
        "city": (str(parsed.get("city")).strip() if parsed.get("city") else None),
        "country": (str(parsed.get("country")).strip() if parsed.get("country") else None),
        "headline": (str(parsed.get("headline")).strip() if parsed.get("headline") else None),
        "education": _entries(parsed.get("education"), ("degree", "institution")),
        "experience": _entries(parsed.get("experience"), ("job_title", "company_name")),
        "skills": _str_list(parsed.get("skills"))[:30],
    }
    return normalized, provider
