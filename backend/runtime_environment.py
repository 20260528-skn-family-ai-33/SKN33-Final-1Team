"""Select an explicit environment and pass only the selected service's settings."""
import os
from pathlib import Path

from backend.app.config import ConfigurationError, read_environment_file
from backend.environment_source import (
    DATABASE, WEB, REVIEW, STORAGE, MODEL, QWEN, GRAPH, MCP, COOKIE,
    PROFILES, PREFIXES, PRIVILEGED, select_service_values, service_file_values,
)


MANAGED_PREFIXES = ("CC_", "DB_", "POSTGRES_", "TEST_DB_", "AUTH_", "REVIEW_", "OCR_", "REQUIREMENTS_", "RUNPOD_", "LOGO_", "VIDEO_", "MODELTEST_", "AWS_", "PG_TOOLS_", "C12_", "C13_", "A02_", "A04_A05_MODEL_")
MANAGED = frozenset().union(*PROFILES.values(), PRIVILEGED, {"IMPORT_ALLOWED_ROOT", "TEST_DB_ENV_FILE", "AF_NEO4J_PASSWORD"})


def runtime_values(component, filename=None, environ=None):
    inherited = os.environ if environ is None else environ
    source = read_environment_file(Path(filename), {}) if filename is not None else {}
    if any(value and (key.startswith(("TEST_DB_", "CC_TEST_", "CC_INTEGRATION_")) or key == "TEST_DB_ENV_FILE")
           for key, value in source.items()):
        raise ConfigurationError("A disposable-test environment must not be used for a service.")
    if source.get("CC_ENV_MODE") == "local_unified" and inherited.get("APP_ENV", source.get("APP_ENV")) != "development":
        raise ConfigurationError("LOCAL_UNIFIED_ENV_REQUIRES_DEVELOPMENT")
    try:
        source = service_file_values(source)
    except ValueError as error:
        raise ConfigurationError(str(error)) from None
    values = source | dict(inherited)
    if any(source.get(key) or inherited.get(key) for key in PRIVILEGED):
        raise ConfigurationError("Service startup requires a runtime file without owner/admin credentials.")
    selected = select_service_values(component, values)
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
