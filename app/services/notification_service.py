"""In-app notifications and best-effort Resend transactional email.

Email failures deliberately never interrupt an application or password-reset
request. Configure a verified Resend sender domain with SPF/DKIM for real
deliverability; the free plan is suitable for early-stage notification volume.
"""

import logging
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote, urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger("wazifny.notify")
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_FREE_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "aol.com", "live.com", "msn.com"}
_warned_configuration = False


def _delivery_configuration_issue() -> str | None:
    """Prevent unsafe production reset emails before they are sent."""
    domain = settings.resend_from_email.rsplit("@", 1)[-1].lower()
    if domain in _FREE_EMAIL_DOMAINS:
        return "RESEND_FROM_EMAIL must use a domain you verify in Resend, not a personal mailbox"
    parsed = urlparse(settings.frontend_url)
    is_local = parsed.hostname in {"localhost", "127.0.0.1", "0.0.0.0"}
    if not parsed.hostname or (not is_local and parsed.scheme != "https") or (is_local and settings.environment.lower() != "development"):
        return "FRONTEND_URL must be the public HTTPS frontend URL (localhost is development-only)"
    return None


def _can_send_transactional_email() -> bool:
    global _warned_configuration
    issue = _delivery_configuration_issue()
    if not issue:
        return True
    if not _warned_configuration:
        logger.error("Resend transactional email is disabled: %s", issue)
        _warned_configuration = True
    return False


async def create_notification(db, user_id: str, type_: str, title: str, content: str) -> None:
    await db.notifications.insert_one({"user_id": user_id, "type": type_, "title": title, "content": content, "is_read": False, "created_at": datetime.now(timezone.utc)})


async def send_email(to_email: str, to_name: str, subject: str, html_body: str) -> bool:
    """Send one HTML/text transactional email through Resend's REST API."""
    if not _can_send_transactional_email():
        return False
    if not settings.resend_api_key:
        logger.warning("Email not sent to %s: RESEND_API_KEY is not configured", to_email)
        return False
    payload: dict = {
        "from": f"{settings.resend_from_name} <{settings.resend_from_email}>",
        # Resend's REST endpoint accepts a recipient email string (unlike
        # Elastic Email's recipient-object format used by the old sender).
        "to": to_email,
        "subject": subject,
        "html": html_body,
        "text": "Please view this message in an HTML-capable email client.",
    }
    if settings.resend_reply_to:
        payload["reply_to"] = settings.resend_reply_to
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {settings.resend_api_key}", "Content-Type": "application/json"}, json=payload)
        if response.status_code in {200, 201}:
            logger.info("Resend email sent to %s: %r", to_email, subject)
            return True
        logger.warning("Resend email failed (%s) to %s: %s", response.status_code, to_email, response.text[:300])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Resend email raised %s: %s", type(exc).__name__, exc)
    return False


def _wrap(inner_html: str) -> str:
    return f'''<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px;color:#0f172a"><div style="font-weight:700;font-size:22px;margin-bottom:22px;color:#0b1224">Wazifny</div>{inner_html}<p style="color:#94a3b8;font-size:12px;margin-top:32px">You are receiving this because you have a Wazifny account.</p></div>'''


async def send_password_reset_email(to_email: str, to_name: str, reset_token: str) -> bool:
    link = f"{settings.frontend_url.rstrip('/')}/reset-password?token={quote(reset_token, safe='')}"
    html = _wrap(f'''<p>Hi {escape(to_name)},</p><p>We received a request to reset your Wazifny password. This link expires in one hour.</p><p><a href="{link}" style="background:#16a34a;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;display:inline-block">Reset Password</a></p><p style="color:#64748b;font-size:12px;word-break:break-all">If the button does not open, copy this link into your browser:<br><a href="{link}">{link}</a></p><p>If you did not request this, you can safely ignore this email.</p>''')
    return await send_email(to_email, to_name, "Reset your Wazifny password", html)


async def send_application_confirmation_email(to_email: str, to_name: str, job_title: str, company_name: str | None) -> bool:
    company = f" at <strong>{escape(company_name)}</strong>" if company_name else ""
    html = _wrap(f'''<p>Hi {escape(to_name)},</p><p>Your application for <strong>{escape(job_title)}</strong>{company} was submitted successfully. You can track its status from your Application Tracker.</p><p><a href="{settings.frontend_url.rstrip('/')}/talent/applications" style="background:#16a34a;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;display:inline-block">View Application</a></p>''')
    return await send_email(to_email, to_name, f"Application submitted: {job_title}", html)


async def send_new_applicant_email(to_email: str, to_name: str, talent_name: str, job_title: str, match_score: int | None) -> bool:
    score = f" — <strong>{match_score}% match</strong>" if match_score is not None else ""
    html = _wrap(f'''<p>Hi {escape(to_name)},</p><p><strong>{escape(talent_name)}</strong> just applied to <strong>{escape(job_title)}</strong>{score}.</p><p>Their full profile is available in your Applicants dashboard.</p><p><a href="{settings.frontend_url.rstrip('/')}/employer/applicants" style="background:#16a34a;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;display:inline-block">Review Applicant</a></p>''')
    return await send_email(to_email, to_name, f"New application: {talent_name} for {job_title}", html)
