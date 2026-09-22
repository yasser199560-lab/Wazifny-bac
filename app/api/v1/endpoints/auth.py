from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import create_access_token, hash_password, hash_reset_token, verify_password
from app.db.mongodb import get_database
from app.schemas.auth import (
    CurrentUserResponse,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.services.notification_service import send_password_reset_email
from app.utils.deps import get_current_user

router = APIRouter()


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest) -> TokenResponse:
    db = get_database()

    if payload.role == "employer" and not (payload.company_name or "").strip():
        raise HTTPException(status_code=422, detail="company_name is required for employers")

    existing = await db.users.find_one({"email": payload.email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    from datetime import datetime, timezone

    user_doc = {
        "full_name": payload.full_name.strip(),
        "email": payload.email.lower(),
        "password_hash": hash_password(payload.password),
        "role": payload.role,  # "talent" | "employer" | "admin"
        "phone": None,
        "is_verified": False,
        "created_at": datetime.now(timezone.utc),
    }
    result = await db.users.insert_one(user_doc)
    user_id = str(result.inserted_id)

    # Create the matching profile shell so downstream steps (CV upload,
    # company setup, etc.) have somewhere to attach data.
    if payload.role == "talent":
        await db.talent_profiles.insert_one(
            {"user_id": user_id, "profile_completion_status": "incomplete"}
        )
    elif payload.role == "employer":
        await db.employer_profiles.insert_one(
            {"user_id": user_id, "company_name": payload.company_name}
        )

    token = create_access_token(subject=user_id, role=payload.role)
    return TokenResponse(
        access_token=token,
        role=payload.role,
        full_name=user_doc["full_name"],
        email=user_doc["email"],
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    """Single unified login for every role.

    The caller never selects a role — we look the account up by email and
    the token (and the `role` field returned alongside it) tells the
    frontend where to route the user next (talent / employer / admin).
    """
    db = get_database()

    user = await db.users.find_one({"email": payload.email.lower()})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user.get("is_blocked"):
        raise HTTPException(status_code=403, detail="This account has been blocked")

    token = create_access_token(subject=str(user["_id"]), role=user["role"])
    return TokenResponse(
        access_token=token,
        role=user["role"],
        full_name=user.get("full_name", ""),
        email=user["email"],
    )


@router.get("/me", response_model=CurrentUserResponse)
async def read_current_user(current_user: dict = Depends(get_current_user)) -> CurrentUserResponse:
    """Return the authenticated user's identity — used by the frontend to
    restore a session on refresh and to know which dashboard to render."""
    return CurrentUserResponse(
        id=current_user["id"],
        full_name=current_user.get("full_name", ""),
        email=current_user["email"],
        role=current_user["role"],
        is_verified=current_user.get("is_verified", False),
    )


@router.post("/forgot-password", status_code=204)
async def forgot_password(payload: ForgotPasswordRequest) -> None:
    """POST /auth/forgot-password — always returns 204 regardless of
    whether the email exists, so this endpoint can't be used to enumerate
    registered accounts. If it does exist, emails a reset link via Elastic
    Email (best-effort — a delivery failure here never surfaces to the
    caller, for the same enumeration-safety reason)."""
    db = get_database()
    user = await db.users.find_one({"email": payload.email.lower()})
    if not user:
        return

    import secrets

    token = secrets.token_urlsafe(32)
    # Do not create a usable reset token if delivery was rejected.  This is
    # especially important when the mail configuration is unsafe: otherwise
    # users see a success message but receive a Gmail-blocked link.
    delivered = await send_password_reset_email(user["email"], user.get("full_name", ""), token)
    if not delivered:
        return

    await db.password_resets.insert_one(
        {
            "user_id": str(user["_id"]),
            "token_hash": hash_reset_token(token),
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "used": False,
            "created_at": datetime.now(timezone.utc),
        }
    )


@router.post("/reset-password", status_code=204)
async def reset_password(payload: ResetPasswordRequest) -> None:
    """POST /auth/reset-password — consumes a one-time token from
    /forgot-password and sets a new password."""
    db = get_database()
    reset_doc = await db.password_resets.find_one(
        {"token_hash": hash_reset_token(payload.token), "used": False}
    )

    if not reset_doc:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")

    # MongoDB always returns naive datetimes (it stores everything as UTC
    # but doesn't preserve tzinfo on read) — reattach it before comparing,
    # or this comparison raises on every single call, seed data or not.
    expires_at = reset_doc["expires_at"].replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")

    from bson import ObjectId

    await db.users.update_one(
        {"_id": ObjectId(reset_doc["user_id"])},
        {"$set": {"password_hash": hash_password(payload.new_password)}},
    )
    await db.password_resets.update_one({"_id": reset_doc["_id"]}, {"$set": {"used": True}})
