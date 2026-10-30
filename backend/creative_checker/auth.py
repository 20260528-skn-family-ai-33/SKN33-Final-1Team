"""Opaque, server-side sessions and double-checked CSRF tokens."""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuthSession, User, utcnow
from .settings import Settings


def password_hash(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain between 12 and 128 characters")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt" or (int(n), int(r), int(p)) != (16384, 8, 1) or len(password) > 128:
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                                n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 320:
        raise ValueError("A valid email address is required")
    return email


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_token(token: str) -> str:
    return digest("csrf:" + token)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def public_user(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin}


def issue_session(db: Session, user: User, settings: Settings) -> tuple[AuthSession, str, str]:
    token = secrets.token_urlsafe(48)
    csrf = csrf_token(token)
    session = AuthSession(user_id=user.id, token_hash=digest(token), csrf_hash=digest(csrf),
                          expires_at=utcnow() + timedelta(hours=settings.session_hours))
    db.add(session)
    db.flush()
    return session, token, csrf


def authenticate(request: Request, db: Session, settings: Settings) -> tuple[User, AuthSession, str]:
    token = request.cookies.get(settings.session_cookie, "")
    if len(token) < 32 or len(token) > 200:
        raise HTTPException(401, "Authentication required")
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest(token)))
    if session is None or session.revoked_at is not None or aware(session.expires_at) <= utcnow():
        raise HTTPException(401, "Session expired or invalid")
    user = db.get(User, session.user_id)
    if user is None or not user.active:
        raise HTTPException(401, "Authentication required")
    csrf = csrf_token(token)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        received = request.headers.get("x-csrf-token", "")
        if len(received) != 64 or not hmac.compare_digest(digest(received), session.csrf_hash):
            raise HTTPException(403, "Invalid CSRF token")
    return user, session, csrf


def ensure_same_origin(request: Request) -> None:
    """Also covers login CSRF, before a session exists."""
    from urllib.parse import urlsplit

    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != request.url.netloc.lower():
            raise HTTPException(403, "Cross-origin requests are not permitted")
