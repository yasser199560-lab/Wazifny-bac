"""FastAPI dependencies: get_current_user, require_role(role), pagination helpers."""

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from app.core.security import decode_access_token
from app.db.mongodb import get_database

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """Decode the bearer token and load the matching user document.

    Raises 401 if the token is missing/invalid/expired or the user no
    longer exists.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(credentials.credentials)
        user_id = payload.get("sub")
        if user_id is None:
            raise JWTError("Missing subject claim")
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    db = get_database()
    try:
        user = await db.users.find_one({"_id": ObjectId(user_id)})
    except InvalidId as exc:
        raise HTTPException(status_code=401, detail="Invalid token subject") from exc

    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    if user.get("is_blocked"):
        raise HTTPException(status_code=403, detail="This account has been blocked")

    user["id"] = str(user.pop("_id"))
    user.pop("password_hash", None)
    return user


async def get_optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict | None:
    """Return the signed-in user when present, while keeping public routes public."""
    if credentials is None:
        return None
    return await get_current_user(credentials)


def require_role(*allowed_roles: str):
    """Dependency factory: restricts an endpoint to one or more roles.

    Usage: `current_user = Depends(require_role("employer", "admin"))`
    """

    async def _check_role(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user.get("role") not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return _check_role


def pagination_params(page: int = 1, page_size: int = 20) -> dict:
    """Shared pagination query params -> Mongo skip/limit."""
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    return {"page": page, "page_size": page_size, "skip": (page - 1) * page_size, "limit": page_size}
