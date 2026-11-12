"""Select service values from an explicitly opted-in local settings file.

Standard library only: desktop launchers use this before any dependencies are
loaded. Administrator settings remain available to explicit management commands.
"""

PRIVILEGED = frozenset({"DB_OWNER_PASSWORD", "POSTGRES_PASSWORD", "CC_LOCAL_DB_PASSWORD", "CC_LOCAL_GRAPH_PASSWORD"})

DATABASE = frozenset("PERSISTENCE_MODE DB_HOST DB_PORT POSTGRES_DB DB_APP_USER DB_APP_PASSWORD DB_OWNER_USER DB_CONNECT_TIMEOUT DB_POOL_SIZE DB_POOL_TIMEOUT DB_SSLMODE DB_SSLROOTCERT APP_ENV".split())
WEB = frozenset("LOCAL_ADMIN_LOGIN DEV_PROJECT_ID AUTH_ALLOWED_ORIGINS AUTH_ALLOWED_HOSTS CSRF_SECRET CURSOR_SECRET AUTH_SESSION_SECONDS AUTH_CSRF_SECONDS AUTH_MAX_CONCURRENT_HASHES WEB_CONCURRENCY UVICORN_WORKERS FRONTEND_DIST TRUSTED_PROXY_IPS".split())
REVIEW = frozenset("STORAGE_ROOT REVIEW_EXECUTOR REVIEW_PIPELINE REVIEW_CONFIG_FILE RUN_TARGET".split())
STORAGE = frozenset("DATABASE_URL CC_DATA_DIR CC_STORAGE_BACKEND CC_S3_BUCKET CC_S3_REGION AWS_REGION AWS_DEFAULT_REGION AWS_PROFILE AWS_CONFIG_FILE AWS_SHARED_CREDENTIALS_FILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN".split())
MODEL = frozenset("CC_QWEN_MODEL CC_QWEN_REVISION".split())
QWEN = MODEL | {"CC_QWEN_BASE_URL", "CC_QWEN_API_KEY"}
GRAPH = frozenset("CC_NEO4J_URI CC_NEO4J_USER CC_NEO4J_PASSWORD".split())
MCP = frozenset("CC_MCP_URL CC_MCP_TOKEN".split())
COOKIE = frozenset("CC_COOKIE_SECURE CC_SESSION_COOKIE CC_SESSION_HOURS".split())
# 2026-11-12 A02 Qwen 실패 뒤 OpenAI 대체 추출(backend/agent_framework/openai_fallback.py와 같은 이름).
OPENAI_FALLBACK = frozenset("A02_OPENAI_FALLBACK A02_OPENAI_MODEL A02_OPENAI_REASONING_EFFORT A02_OPENAI_API_KEY OPENAI_API_KEY creative_checker_openapi".split())
PROFILES = {
    "api": STORAGE | MODEL | GRAPH | COOKIE,
    "worker": frozenset({"DATABASE_URL"}) | MODEL | MCP,
    "projection": frozenset({"DATABASE_URL"}) | GRAPH,
    "mcp": STORAGE | QWEN | MCP,
    "v1": DATABASE | WEB | REVIEW | {"creative_checker_openapi"},
    "v1-worker": DATABASE | REVIEW | {"LOCAL_ADMIN_LOGIN"},
    "framework-worker": DATABASE | QWEN | GRAPH | OPENAI_FALLBACK | {"STORAGE_ROOT", "A02_QWEN_API_KEY"},
    "framework-projection": DATABASE | GRAPH,
    # 영상 API(legacy 프로필)의 계약서 A02가 포스터 A02와 같은 Qwen3.5-4B 서버를 쓴다.
    "legacy": DATABASE | WEB | REVIEW | QWEN | OPENAI_FALLBACK | {"LEGACY_READ_ONLY", "CC_WEB_POSTGRES_ENV_FILE", "FFMPEG_BIN", "A02_QWEN_API_KEY"},
}
PREFIXES = {
    "legacy": ("OCR_", "REQUIREMENTS_", "RUNPOD_", "LOGO_", "VIDEO_", "MODELTEST_", "REVIEW_MODELS_"),
    "v1": ("OCR_",),
    "v1-worker": ("OCR_",),
    "framework-worker": ("A04_A05_MODEL_",),
}


def select_service_values(component, values):
    allowed = PROFILES[component]
    prefixes = PREFIXES.get(component, ())
    return {key: value for key, value in values.items() if key in allowed or key.startswith(prefixes)}


def service_file_values(values):
    mode = values.get("CC_ENV_MODE", "")
    if mode in ("", "separate"):
        return dict(values)
    if mode != "local_unified" or values.get("APP_ENV") != "development":
        raise ValueError("LOCAL_UNIFIED_ENV_REQUIRES_DEVELOPMENT")
    excluded = PRIVILEGED | {"CC_ENV_MODE", "AF_NEO4J_PASSWORD"}
    return {key: value for key, value in values.items() if key not in excluded}
