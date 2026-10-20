"""Identity SQL only. Callers own connections, locks and commit decisions."""
from sqlalchemy import select,update,func,and_
from uuid import uuid4
from datetime import timedelta
from ..schema.identity import users,user_sessions,projects,project_members


def user_by_email(connection,email):
    return connection.execute(select(users).where(users.c.email==email,users.c.account_kind=='email')).mappings().one_or_none()


def developer(connection):
    return connection.execute(select(users).where(users.c.account_kind=='developer')).mappings().one_or_none()


def user(connection,user_id,*,lock=None):
    query=select(users).where(users.c.user_id==user_id)
    if lock:query=query.with_for_update(read=lock=='share')
    return connection.execute(query).mappings().one_or_none()


def session(connection,token_hash,*,lock=False):
    query=select(user_sessions).where(user_sessions.c.token_hash==token_hash)
    if lock:query=query.with_for_update()
    return connection.execute(query).mappings().one_or_none()


def session_state(connection,token_hash):
    row=connection.execute(select(users,user_sessions.c.session_id,user_sessions.c.token_hash).join(
        user_sessions,user_sessions.c.user_id==users.c.user_id).where(user_sessions.c.token_hash==token_hash,
        user_sessions.c.revoked_at.is_(None),user_sessions.c.expires_at>func.clock_timestamp(),users.c.is_active)).mappings().one_or_none()
    return dict(row) if row else None


def revoke_session(connection,session_id,now):
    connection.execute(update(user_sessions).where(user_sessions.c.session_id==session_id,
        user_sessions.c.revoked_at.is_(None)).values(revoked_at=now))


def revoke_all(connection,user_id,now):
    connection.execute(update(user_sessions).where(user_sessions.c.user_id==user_id,user_sessions.c.revoked_at.is_(None)).values(revoked_at=now))


def new_session(connection,user_id,token_hash,now):
    session_id=uuid4()
    connection.execute(user_sessions.insert().values(session_id=session_id,user_id=user_id,token_hash=token_hash,
        created_at=now,expires_at=now+timedelta(seconds=28800)))
    return session_id


def project_ids(connection,user_ids):
    return connection.execute(select(project_members.c.project_id).where(project_members.c.user_id.in_(user_ids)).distinct().order_by(project_members.c.project_id)).scalars().all()


def lock_projects(connection,project_ids):
    for project_id in sorted(set(project_ids)):
        connection.execute(select(projects.c.project_id).where(projects.c.project_id==project_id).with_for_update(key_share=True)).all()


def valid_development_setup(connection,user_id,project_id):
    return bool(connection.execute(select(projects.c.project_id).join(project_members,
        project_members.c.project_id==projects.c.project_id).where(projects.c.project_id==project_id,
        projects.c.business_state=='active',project_members.c.user_id==user_id,
        project_members.c.is_active,project_members.c.role=='owner')).first())
