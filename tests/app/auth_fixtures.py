"""Synthetic app configuration only. No real account/credentials or implicit .env reads."""
from uuid import UUID

DEV_PROJECT=UUID('2ae49810-1526-47a5-b09f-43d0ab955ab2')
PASSWORD='합성 비밀번호 123456789!'


def auth_environment(database=None,**overrides):
    values={'PERSISTENCE_MODE':'postgres','DB_HOST':'127.0.0.1','DB_PORT':'5432',
        'POSTGRES_DB':'synthetic_db','DB_APP_USER':'creative_checker_app','DB_APP_PASSWORD':'synthetic-not-real-db-secret',
        'APP_ENV':'test','LOCAL_ADMIN_LOGIN':'1','DEV_PROJECT_ID':str(DEV_PROJECT),
        'AUTH_ALLOWED_ORIGINS':'http://localhost:8000','AUTH_ALLOWED_HOSTS':'localhost',
        'CSRF_SECRET':'synthetic-csrf-secret-with-at-least-32-bytes',
        'CURSOR_SECRET':'synthetic-cursor-secret-with-at-least-32-bytes'}
    if database:
        values.update(DB_HOST=database.settings.database.host,DB_PORT=str(database.settings.database.port),
            POSTGRES_DB=database.name,DB_APP_USER=database.settings.database.username,
            DB_APP_PASSWORD=database.settings.database.password.reveal())
    values.update(overrides)
    return values
