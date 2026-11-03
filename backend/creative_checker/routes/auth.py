"""HTTP routes for auth."""

from datetime import timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import select, update

from backend.creative_checker import contracts as C, services as S, auth
from backend.creative_checker.http import Admin, Db, Identity
from backend.creative_checker.models import AuthSession, User, utcnow


def create_router(settings):
    router = APIRouter()

    @router.post("/api/auth/login")
    def login(body: C.Login, request: Request, response: Response, db: Db):
        try:
            email = auth.normalize_email(body.email)
        except ValueError:
            S.fail(401, "invalid_credentials", "Email or password is incorrect")
        user = db.scalar(select(User).where(User.email == email).with_for_update())
        if user is None:
            # Same work factor for unknown accounts; no account information is returned.
            auth.verify_password(body.password, "scrypt$16384$8$1$" + "00" * 16 + "$" + "00" * 32)
            S.fail(401, "invalid_credentials", "Email or password is incorrect")
        if not user.active or user.locked_until and auth.aware(user.locked_until) > utcnow():
            S.fail(401, "invalid_credentials", "Email or password is incorrect")
        if not auth.verify_password(body.password, user.password_hash):
            user.failed_login_count += 1
            if user.failed_login_count >= 5:
                user.failed_login_count = 0
                user.locked_until = utcnow() + timedelta(minutes=5)
            S.commit(db)
            S.fail(401, "invalid_credentials", "Email or password is incorrect")
        user.failed_login_count, user.locked_until = 0, None
        old_token = request.cookies.get(settings.session_cookie)
        if old_token:
            db.execute(update(AuthSession).where(AuthSession.token_hash == auth.digest(old_token)).values(revoked_at=utcnow()))
        _session, token, csrf = auth.issue_session(db, user, settings)
        S.commit(db)
        response.set_cookie(settings.session_cookie, token, max_age=settings.session_hours * 3600,
                            httponly=True, secure=settings.cookie_secure, samesite="lax", path="/api")
        return {"user": auth.public_user(user), "csrf_token": csrf}

    @router.get("/api/auth/me")
    def me(identity: Identity):
        return {"user": auth.public_user(identity[0]), "csrf_token": identity[2]}

    @router.post("/api/auth/logout")
    def logout(response: Response, db: Db, identity: Identity):
        identity[1].revoked_at = utcnow()
        S.commit(db)
        response.delete_cookie(settings.session_cookie, path="/api", secure=settings.cookie_secure, httponly=True, samesite="lax")
        return {"ok": True}

    @router.post("/api/auth/password")
    def change_password(body: C.PasswordChange, db: Db, identity: Identity):
        user = db.scalar(select(User).where(User.id == identity[0].id).with_for_update())
        if not auth.verify_password(body.current_password, user.password_hash):
            S.fail(403, "invalid_password", "The current password is incorrect")
        user.password_hash = auth.password_hash(body.new_password)
        db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != identity[1].id,
                                             AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
        S.commit(db)
        return {"ok": True}

    @router.get("/api/admin/users")
    def users(db: Db, _admin: Admin):
        return {"items": [auth.public_user(user) for user in db.scalars(select(User).order_by(User.created_at, User.id))]}

    @router.post("/api/admin/users", status_code=201)
    def add_user(body: C.CreateUser, db: Db, _admin: Admin):
        try:
            email = auth.normalize_email(body.email)
            encoded = auth.password_hash(body.password)
        except ValueError as exc:
            S.fail(422, "invalid_user", str(exc))
        if not body.name.strip():
            S.fail(422, "invalid_user", "User name is required")
        user = User(email=email, name=body.name.strip(), password_hash=encoded, is_admin=body.is_admin)
        db.add(user)
        S.commit(db)
        return auth.public_user(user)

    return router
