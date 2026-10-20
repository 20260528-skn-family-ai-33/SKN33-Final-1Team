"""Explicit v1 web settings. Existing C01/legacy settings remain unchanged."""
from dataclasses import dataclass
from pathlib import Path
from ipaddress import ip_address
import os
from urllib.parse import urlsplit
from uuid import UUID

from .config import ConfigurationError,Secret,Settings,load_settings


@dataclass(frozen=True,slots=True)
class V1Settings:
    persistence: Settings
    environment: str
    developer_login: bool
    developer_project: UUID | None
    origins: tuple[str,...]
    hosts: tuple[str,...]
    csrf_secret: Secret
    cursor_secret: Secret
    storage_root: Path | None = None
    review_pipeline: str = "disabled"
    frontend_dist: Path | None = None
    review_executor: str = 'background_tasks'
    review_config_file: Path | None = None

    @property
    def database(self):return self.persistence.database
    @property
    def persistence_mode(self):return self.persistence.persistence_mode
    @property
    def secure(self):return self.environment=='production'
    @property
    def session_cookie(self):return '__Host-baro_session' if self.secure else 'baro_session'
    @property
    def csrf_cookie(self):return '__Host-csrftoken' if self.secure else 'csrftoken'
    @property
    def seed_cookie(self):return '__Host-baro_csrf_seed' if self.secure else 'baro_csrf_seed'


def is_loopback(host):
    if host=='localhost':return True
    try:return ip_address(host).is_loopback
    except ValueError:return False


def origin(value):
    try:
        parsed=urlsplit(value)
        if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in ('','/') or parsed.query or parsed.fragment
            or parsed.port==0 or '*' in value or any(c.isspace() for c in value)):
            return None
        host=parsed.hostname.lower()
        authority='['+host+']' if ':' in host else host
        port=parsed.port
        if port and port!=({'http':80,'https':443}[parsed.scheme]):authority+=':'+str(port)
        return parsed.scheme+'://'+authority
    except ValueError:return None


def load_v1_settings(environ=None):
    values=os.environ if environ is None else environ
    environment=values.get('APP_ENV','production')
    if environment not in ('development','test','production'):raise ConfigurationError('APP_ENV must be development, test or production.')
    if any(values.get(k) for k in ('DB_OWNER_PASSWORD','POSTGRES_PASSWORD')):
        raise ConfigurationError('The web profile must not receive owner/admin credentials.')
    persistence=load_settings(values)
    if persistence.persistence_mode!='postgres':raise ConfigurationError('The v1 profile requires PERSISTENCE_MODE=postgres; legacy uses its existing local entrypoint.')
    flag=values.get('LOCAL_ADMIN_LOGIN','0')
    if flag not in ('0','1') or environment=='production' and flag=='1':raise ConfigurationError('Development login must be explicitly disabled in production.')
    for key,expected in (('AUTH_SESSION_SECONDS','28800'),('AUTH_CSRF_SECONDS','900'),('AUTH_MAX_CONCURRENT_HASHES','2'),('WEB_CONCURRENCY','1'),('UVICORN_WORKERS','1')):
        if values.get(key,expected)!=expected:raise ConfigurationError(key+' must use the first v1 fixed value.')
    raw=values.get('AUTH_ALLOWED_ORIGINS','').split(',')
    origins=tuple(origin(v.strip()) for v in raw)
    if not origins or any(v is None for v in origins) or len(set(origins))!=len(origins):raise ConfigurationError('Explicit unique AUTH_ALLOWED_ORIGINS are required.')
    if environment=='production' and any(not v.startswith('https://') for v in origins):raise ConfigurationError('Production origins require HTTPS.')
    if environment!='production' and any(not is_loopback(urlsplit(v).hostname) for v in origins):raise ConfigurationError('Development origins must be loopback.')
    hosts=tuple(v.strip().lower() for v in values.get('AUTH_ALLOWED_HOSTS',','.join(sorted({urlsplit(v).hostname for v in origins}))).split(','))
    if not hosts or any(not h or '*' in h or '/' in h or any(c.isspace() for c in h) for h in hosts):raise ConfigurationError('Explicit exact hosts are required.')
    if any(urlsplit(v).hostname not in hosts for v in origins):raise ConfigurationError('Configured origin hosts must be allowed.')
    secrets=[]
    for key in ('CSRF_SECRET','CURSOR_SECRET'):
        secret=values.get(key,'')
        if len(secret.encode('utf-8'))<32 or any(c in secret for c in '\x00\r\n') or secret.startswith('change-me-'):
            raise ConfigurationError(key+' requires a distinct secret of at least 32 UTF-8 bytes.')
        secrets.append(secret)
    if secrets[0]==secrets[1]:raise ConfigurationError('CSRF_SECRET and CURSOR_SECRET must differ.')
    try:project=UUID(values['DEV_PROJECT_ID']) if values.get('DEV_PROJECT_ID') else None
    except (ValueError,TypeError):raise ConfigurationError('DEV_PROJECT_ID must be a UUID.') from None
    if flag=='1' and project is None:raise ConfigurationError('Explicit DEV_PROJECT_ID is required for development login.')
    root = values.get('STORAGE_ROOT')
    if root and (not Path(root).is_absolute() or '..' in Path(root).parts):
        raise ConfigurationError('STORAGE_ROOT must be an absolute dedicated path without parent traversal.')
    review_pipeline=values.get('REVIEW_PIPELINE','disabled')
    executor=values.get('REVIEW_EXECUTOR','background_tasks')
    if executor not in ('background_tasks','postgres_worker'):raise ConfigurationError('Invalid REVIEW_EXECUTOR.')
    config_file=values.get('REVIEW_CONFIG_FILE')
    if executor=='postgres_worker':
        if not root or review_pipeline!='disabled' or not config_file or not Path(config_file).is_absolute() or '..' in Path(config_file).parts:
            raise ConfigurationError('Worker admission requires STORAGE_ROOT, REVIEW_PIPELINE=disabled and an absolute REVIEW_CONFIG_FILE.')
    elif config_file:raise ConfigurationError('REVIEW_CONFIG_FILE is only for postgres_worker admission.')
    if review_pipeline not in ('disabled','paddle-only'):
        raise ConfigurationError('REVIEW_PIPELINE must be disabled or the explicit local paddle-only adapter.')
    if review_pipeline!='disabled' and (not root or values.get('RUN_TARGET','local')!='local'):
        raise ConfigurationError('Background review execution requires STORAGE_ROOT and local execution.')
    frontend = values.get('FRONTEND_DIST')
    if frontend and (not Path(frontend).is_absolute() or '..' in Path(frontend).parts):
        raise ConfigurationError('FRONTEND_DIST must be an absolute build directory without traversal.')
    return V1Settings(persistence,environment,flag=='1',project,origins,hosts,Secret(secrets[0]),Secret(secrets[1]),Path(root) if root else None,review_pipeline,Path(frontend) if frontend else None,executor,Path(config_file) if config_file else None)
