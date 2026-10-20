"""Explicit, side-effect-free persistence settings; the legacy API does not use these yet."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Mapping


class ConfigurationError(ValueError):
    code = "DB_CONFIGURATION_INVALID"


@dataclass(frozen=True, slots=True)
class Secret:
    _value: str = field(repr=False)

    def __repr__(self) -> str:
        return "Secret('***')"

    def __str__(self) -> str:
        return "***"

    def reveal(self) -> str:
        return self._value


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    host: str
    port: int
    database: str
    username: str
    password: Secret
    connect_timeout: int = 5
    pool_size: int = 5
    pool_timeout: int = 5


@dataclass(frozen=True, slots=True)
class Settings:
    persistence_mode: str = "legacy_json"
    database: DatabaseSettings | None = None


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "")
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise ConfigurationError(f"{name} is required and must be a single line.")
    return value


def _integer(values: Mapping[str, str], name: str, default: int, maximum: int) -> int:
    raw = values.get(name, str(default))
    if not re.fullmatch(r"[0-9]{1,5}", raw):
        raise ConfigurationError(f"{name} must be a positive integer.")
    value = int(raw)
    if not 1 <= value <= maximum:
        raise ConfigurationError(f"{name} must be between 1 and {maximum}.")
    return value


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Read only the supplied mapping (or process environment); never auto-load .env."""
    values = os.environ if environ is None else environ
    mode = values.get("PERSISTENCE_MODE", "legacy_json")
    if mode not in {"legacy_json", "postgres"}:
        raise ConfigurationError("PERSISTENCE_MODE must be legacy_json or postgres.")
    if mode == "legacy_json":
        return Settings()
    host = _required(values, "DB_HOST")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", host):
        raise ConfigurationError("DB_HOST must be a hostname or an IP address, without a URL or options.")
    database = _required(values, "POSTGRES_DB")
    username = _required(values, "DB_APP_USER")
    for name, value in (("POSTGRES_DB", database), ("DB_APP_USER", username)):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value):
            raise ConfigurationError(f"{name} must be an ASCII PostgreSQL identifier of at most 63 bytes.")
    if username == values.get("DB_OWNER_USER", "creative_checker_owner") or username == "postgres":
        raise ConfigurationError("DB_APP_USER must be the application role, not an owner/admin role.")
    password = _required(values, "DB_APP_PASSWORD")
    if not password.strip() or password.startswith("change-me-"):
        raise ConfigurationError("DB_APP_PASSWORD must be replaced with a nonblank secret.")
    return Settings(mode, DatabaseSettings(
        host=host, port=_integer(values, "DB_PORT", 5432, 65535),
        database=database, username=username, password=Secret(password),
        connect_timeout=_integer(values, "DB_CONNECT_TIMEOUT", 5, 60),
        pool_size=_integer(values, "DB_POOL_SIZE", 5, 20),
        pool_timeout=_integer(values, "DB_POOL_TIMEOUT", 5, 60),
    ))


def read_environment_file(filename: Path, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Read explicit literal KEY=value lines; process values win. No shell expansion."""
    try:
        data = filename.read_bytes()
        if len(data) > 65536:
            raise ConfigurationError("Environment file exceeds 64 KiB.")
        source = data.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise ConfigurationError("Cannot read the explicit UTF-8 environment file.") from None
    values: dict[str, str] = {}
    for number, original in enumerate(source.splitlines(), 1):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in values:
            raise ConfigurationError(f"Invalid or duplicate environment key on line {number}.")
        if value.startswith(("'", '"')):
            match = re.fullmatch(r"(['\"])(.*?)\1\s*(?:#.*)?", value)
            if not match:
                raise ConfigurationError(f"Invalid single-line quoted value on line {number}.")
            value = match[2]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
    values.update(os.environ if environ is None else environ)
    return values
