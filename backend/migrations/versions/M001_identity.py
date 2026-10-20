"""M001 identity: frozen explicit DDL, without live schema imports, FK, indexes or seed."""
from alembic import context, op
from alembic.util import CommandError
from sqlalchemy import Boolean, CheckConstraint, Column, PrimaryKeyConstraint, Text, UniqueConstraint, VARCHAR, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID, dialect

revision = "M001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    if not context.is_offline_mode():
        existing = op.get_bind().exec_driver_sql("""
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
              AND c.relname <> 'alembic_version' LIMIT 1
        """).first()
        if existing:
            raise CommandError("M001 requires an empty public schema; existing objects are never overwritten.")
    settings = context.config.attributes["migration_settings"]
    quote = dialect().identifier_preparer.quote
    owner, app = quote(settings.owner_user), quote(settings.app_user)
    # A per-schema REVOKE cannot undo global grants; revoke both scopes, including PUBLIC.
    for scope in ("", " IN SCHEMA public"):
        for objects in ("TABLES", "SEQUENCES"):
            op.execute(text(f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner}{scope} REVOKE ALL ON {objects} FROM {app}, PUBLIC"))
    # Alembic creates its table before upgrade(); remove any inherited default grants too.
    op.execute(text(f"REVOKE ALL ON TABLE public.alembic_version FROM {app}, PUBLIC"))

    op.create_table(
        "users",
        Column("user_id", UUID(as_uuid=True), nullable=False, comment="사용자 ID"),
        Column("account_kind", VARCHAR(20), nullable=False, server_default=text("'email'"), comment="계정 종류"),
        Column("email", VARCHAR(254), nullable=True, comment="정식 로그인 이메일"),
        Column("password_hash", Text, nullable=True, comment="정식 비밀번호 해시"),
        Column("display_name", VARCHAR(100), nullable=False, comment="표시 이름"),
        Column("is_active", Boolean, nullable=False, server_default=text("true"), comment="활성 여부"),
        Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="생성 시각"),
        Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="수정 시각"),
        PrimaryKeyConstraint("user_id", name="pk_users"),
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint("isfinite(created_at) AND isfinite(updated_at) AND updated_at >= created_at", name="ck_users_times"),
        CheckConstraint("""display_name ~ '[^[:space:]]' AND (
            (account_kind = 'email' AND email IS NOT NULL AND password_hash IS NOT NULL
             AND email ~ '^[!-~]+$' AND email = lower(email) AND password_hash ~ '[^[:space:]]')
            OR (account_kind = 'developer' AND email IS NULL AND password_hash IS NULL))""", name="ck_users_domain"),
        comment="사용자", schema="public",
    )

    op.create_table(
        "user_sessions",
        Column("session_id", UUID(as_uuid=True), nullable=False, comment="세션 ID"),
        Column("user_id", UUID(as_uuid=True), nullable=False, comment="사용자 ID"),
        Column("token_hash", VARCHAR(64), nullable=False, comment="원문 토큰의 SHA256"),
        Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="발급 시각"),
        Column("expires_at", TIMESTAMP(timezone=True), nullable=False, comment="만료 시각"),
        Column("revoked_at", TIMESTAMP(timezone=True), nullable=True, comment="폐기 시각"),
        PrimaryKeyConstraint("session_id", name="pk_user_sessions"),
        UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_user_sessions_hashes"),
        CheckConstraint("isfinite(created_at) AND isfinite(expires_at) AND (revoked_at IS NULL OR isfinite(revoked_at))", name="ck_user_sessions_times"),
        CheckConstraint("expires_at > created_at AND (revoked_at IS NULL OR revoked_at >= created_at)", name="ck_user_sessions_domain"),
        comment="사용자 세션", schema="public",
    )

    op.create_table(
        "projects",
        Column("project_id", UUID(as_uuid=True), nullable=False, comment="프로젝트 ID"),
        Column("created_by", UUID(as_uuid=True), nullable=False, comment="생성 사용자 ID"),
        Column("name", VARCHAR(200), nullable=False, comment="프로젝트명"),
        Column("purpose", Text, nullable=True, comment="발주 및 검수 목적"),
        Column("use_channels", JSONB, nullable=False, server_default=text("'[]'::jsonb"), comment="예정 사용처 문자열 배열"),
        Column("due_at", TIMESTAMP(timezone=True), nullable=True, comment="납기"),
        Column("business_state", VARCHAR(40), nullable=False, server_default=text("'active'"), comment="업무 상태"),
        Column("archived_at", TIMESTAMP(timezone=True), nullable=True, comment="보관 전환 시각"),
        Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="생성 시각"),
        Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="수정 시각"),
        PrimaryKeyConstraint("project_id", name="pk_projects"),
        CheckConstraint("""isfinite(created_at) AND isfinite(updated_at) AND updated_at >= created_at
            AND (due_at IS NULL OR isfinite(due_at))
            AND (archived_at IS NULL OR (isfinite(archived_at) AND archived_at >= created_at))""", name="ck_projects_times"),
        CheckConstraint("""jsonb_typeof(use_channels) = 'array'
            AND NOT jsonb_path_exists(use_channels, '$[*] ? (@.type() != "string")')""", name="ck_projects_json_shapes"),
        CheckConstraint("""name ~ '[^[:space:]]' AND (
            (business_state = 'active' AND archived_at IS NULL)
            OR (business_state = 'archived' AND archived_at IS NOT NULL))""", name="ck_projects_domain"),
        comment="프로젝트", schema="public",
    )

    op.create_table(
        "project_members",
        Column("project_id", UUID(as_uuid=True), nullable=False, comment="프로젝트 ID"),
        Column("user_id", UUID(as_uuid=True), nullable=False, comment="사용자 ID"),
        Column("role", VARCHAR(40), nullable=False, server_default=text("'viewer'"), comment="프로젝트 역할"),
        Column("is_active", Boolean, nullable=False, server_default=text("true"), comment="참여 활성 여부"),
        Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="최초 참여 시각"),
        Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="변경 시각"),
        PrimaryKeyConstraint("project_id", "user_id", name="pk_project_members"),
        CheckConstraint("isfinite(created_at) AND isfinite(updated_at) AND updated_at >= created_at", name="ck_project_members_times"),
        CheckConstraint("role IN ('owner', 'editor', 'viewer')", name="ck_project_members_domain"),
        comment="프로젝트 참여자", schema="public",
    )


def downgrade():
    raise CommandError("M001 destructive downgrade is disabled; use a reviewed restore or recreate an isolated test DB.")
