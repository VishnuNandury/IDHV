"""Mock auth: bcrypt password hashes + HS256 JWT bearer tokens."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def create_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=settings.token_ttl_minutes)}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def _unauthorized(msg: str = "Not authenticated") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=msg, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                     db: Session = Depends(get_db)) -> User:
    if creds is None or not creds.credentials:
        raise _unauthorized()
    try:
        payload = jwt.decode(creds.credentials, settings.secret_key, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        raise _unauthorized("Invalid or expired token") from None
    user = db.get(User, user_id)
    if user is None:
        raise _unauthorized("User no longer exists")
    return user


def require_role(*roles: str):
    """Dependency factory: ``Depends(require_role("admin"))`` -> returns the current user or 403."""

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=f"Requires role: {', '.join(roles)}")
        return user

    return _dep
