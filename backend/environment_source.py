"""Select service values from an explicitly opted-in local settings file.

Standard library only: desktop launchers use this before any dependencies are
loaded. Administrator settings remain available to explicit management commands.
"""

PRIVILEGED = frozenset({"DB_OWNER_PASSWORD", "POSTGRES_PASSWORD", "CC_LOCAL_DB_PASSWORD", "CC_LOCAL_GRAPH_PASSWORD"})


def service_file_values(values):
    mode = values.get("CC_ENV_MODE", "")
    if mode in ("", "separate"):
        return dict(values)
    if mode != "local_unified" or values.get("APP_ENV") != "development":
        raise ValueError("LOCAL_UNIFIED_ENV_REQUIRES_DEVELOPMENT")
    excluded = PRIVILEGED | {"CC_ENV_MODE", "AF_NEO4J_PASSWORD"}
    return {key: value for key, value in values.items() if key not in excluded}
