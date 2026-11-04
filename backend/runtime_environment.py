"""Select an explicit environment and pass only the selected service's settings."""
import os
from pathlib import Path

from backend.app.config import ConfigurationError, read_environment_file


DATABASE = frozenset("PERSISTENCE_MODE DB_HOST DB_PORT POSTGRES_DB DB_APP_USER DB_APP_PASSWORD DB_OWNER_USER DB_CONNECT_TIMEOUT DB_POOL_SIZE DB_POOL_TIMEOUT APP_ENV".split())
WEB = frozenset("LOCAL_ADMIN_LOGIN DEV_PROJECT_ID AUTH_ALLOWED_ORIGINS AUTH_ALLOWED_HOSTS CSRF_SECRET CURSOR_SECRET AUTH_SESSION_SECONDS AUTH_CSRF_SECONDS AUTH_MAX_CONCURRENT_HASHES WEB_CONCURRENCY UVICORN_WORKERS FRONTEND_DIST TRUSTED_PROXY_IPS".split())
REVIEW = frozenset("STORAGE_ROOT REVIEW_EXECUTOR REVIEW_PIPELINE REVIEW_CONFIG_FILE RUN_TARGET".split())
STORAGE = frozenset("DATABASE_URL CC_DATA_DIR CC_STORAGE_BACKEND CC_S3_BUCKET CC_S3_REGION AWS_REGION AWS_DEFAULT_REGION AWS_PROFILE AWS_CONFIG_FILE AWS_SHARED_CREDENTIALS_FILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN".split())
MODEL = frozenset("CC_QWEN_MODEL CC_QWEN_REVISION".split())
QWEN = MODEL | {"CC_QWEN_BASE_URL", "CC_QWEN_API_KEY"}
GRAPH = frozenset("CC_NEO4J_URI CC_NEO4J_USER CC_NEO4J_PASSWORD".split())
MCP = frozenset("CC_MCP_URL CC_MCP_TOKEN".split())
COOKIE = frozenset("CC_COOKIE_SECURE CC_SESSION_COOKIE CC_SESSION_HOURS".split())
PROFILES = {
    "api": STORAGE | MODEL | GRAPH | COOKIE,
    "worker": frozenset({"DATABASE_URL"}) | MODEL | MCP,
    "projection": frozenset({"DATABASE_URL"}) | GRAPH,
    "mcp": STORAGE | QWEN | MCP,
    "v1": DATABASE | WEB | REVIEW,
    "v1-worker": DATABASE | REVIEW | {"LOCAL_ADMIN_LOGIN"},
    "framework-worker": DATABASE | QWEN | {"STORAGE_ROOT", "A02_QWEN_API_KEY"},
    "legacy": DATABASE | WEB | REVIEW | {"LEGACY_READ_ONLY", "CC_WEB_POSTGRES_ENV_FILE", "FFMPEG_BIN"},
}
PREFIXES = {
    "legacy": ("OCR_", "REQUIREMENTS_", "RUNPOD_", "LOGO_", "VIDEO_", "MODELTEST_", "REVIEW_MODELS_"),
    "v1": ("OCR_",),
    "v1-worker": ("OCR_",),
    "framework-worker": ("A04_A05_MODEL_",),
}
PRIVILEGED = frozenset({"DB_OWNER_PASSWORD", "POSTGRES_PASSWORD", "CC_LOCAL_DB_PASSWORD", "CC_LOCAL_GRAPH_PASSWORD"})
MANAGED_PREFIXES = ("CC_", "DB_", "POSTGRES_", "TEST_DB_", "AUTH_", "REVIEW_", "OCR_", "REQUIREMENTS_", "RUNPOD_", "LOGO_", "VIDEO_", "MODELTEST_", "AWS_", "PG_TOOLS_", "C12_", "C13_", "A02_", "A04_A05_MODEL_")
MANAGED = frozenset().union(*PROFILES.values(), PRIVILEGED, {"IMPORT_ALLOWED_ROOT", "TEST_DB_ENV_FILE"})


def runtime_values(component, filename=None, environ=None):
    inherited = os.environ if environ is None else environ
    source = read_environment_file(Path(filename), {}) if filename is not None else {}
    if any(value and (key.startswith(("TEST_DB_", "CC_TEST_", "CC_INTEGRATION_")) or key == "TEST_DB_ENV_FILE")
           for key, value in source.items()):
        raise ConfigurationError("A disposable-test environment must not be used for a service.")
    values = source | dict(inherited)
    if any(source.get(key) or inherited.get(key) for key in PRIVILEGED):
        raise ConfigurationError("Service startup requires a runtime file without owner/admin credentials.")
    allowed = PROFILES[component]
    prefixes = PREFIXES.get(component, ())
    selected = {key: value for key, value in values.items() if key in allowed or key.startswith(prefixes)}
    if component == "v1-worker":
        # A shared file can omit these; explicit incompatible values still fail validation.
        selected.setdefault("REVIEW_EXECUTOR", "postgres_worker")
        selected.setdefault("REVIEW_PIPELINE", "paddle-only")
        selected.setdefault("RUN_TARGET", "local")
        selected.setdefault("LOCAL_ADMIN_LOGIN", "0")
    return selected


def activate_runtime(component, filename=None):
    values = runtime_values(component, filename)
    for key in tuple(os.environ):
        if key in MANAGED or key.startswith(MANAGED_PREFIXES):
            del os.environ[key]
    os.environ.update(values)
    return values
