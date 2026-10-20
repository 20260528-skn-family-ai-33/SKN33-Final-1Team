"""Settings and disabled imports work without PostgreSQL or optional DB packages."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from backend.app.config import ConfigurationError, load_settings, read_environment_file
from scripts.check_database import main


def app_environment(**changes):
    return {"PERSISTENCE_MODE": "postgres", "DB_HOST": "127.0.0.1", "DB_PORT": "5432",
            "POSTGRES_DB": "creative_checker", "DB_APP_USER": "creative_checker_app",
            "DB_APP_PASSWORD": "unit-only-@:/?#%$' secret", **changes}


def test_disabled_mode_ignores_missing_or_broken_database_settings():
    settings = load_settings({"DB_PORT": "bad", "DB_APP_PASSWORD": ""})
    assert settings.persistence_mode == "legacy_json" and settings.database is None


@pytest.mark.parametrize("changes", [
    {"PERSISTENCE_MODE": ""}, {"PERSISTENCE_MODE": "sqlite"}, {"DB_HOST": ""},
    {"DB_HOST": "postgresql://localhost"}, {"DB_HOST": "localhost options=unsafe"},
    {"DB_PORT": "0"}, {"DB_PORT": "65536"}, {"DB_PORT": "1.2"},
    {"POSTGRES_DB": "postgres?options=unsafe"}, {"DB_APP_USER": "postgres"},
    {"DB_APP_USER": "creative_checker_owner"}, {"DB_APP_USER": "custom_owner", "DB_OWNER_USER": "custom_owner"},
    {"DB_APP_PASSWORD": ""}, {"DB_APP_PASSWORD": "   "}, {"DB_APP_PASSWORD": "change-me-app-password"},
    {"DB_APP_PASSWORD": "do-not-leak\n"}, {"DB_POOL_SIZE": "0"}, {"DB_POOL_SIZE": "21"},
    {"DB_CONNECT_TIMEOUT": "61"}, {"DB_POOL_TIMEOUT": "-1"},
])
def test_invalid_settings_are_rejected_without_echoing_values(changes):
    with pytest.raises(ConfigurationError) as caught:
        load_settings(app_environment(**changes))
    assert "do-not-leak" not in str(caught.value)


def test_password_is_literal_and_owner_credentials_are_excluded():
    values = app_environment(DB_OWNER_PASSWORD="owner-never-used")
    settings = load_settings(values)
    assert settings.database.password.reveal() == values["DB_APP_PASSWORD"]
    assert "owner-never-used" not in repr(settings)
    assert values["DB_APP_PASSWORD"] not in repr(settings)
    assert str(settings.database.password) == "***"
    assert not hasattr(settings.database, "owner_password")


def test_explicit_env_file_is_literal_and_exported_values_win(tmp_path):
    filename = tmp_path / "settings.env"
    filename.write_text("# comment\nexport DB_PORT=5432\nDB_APP_PASSWORD='literal$(never-run)#value'\n"
                        'DB_HOST="127.0.0.1" # comment\nPERSISTENCE_MODE=legacy_json\n', encoding="utf-8")
    values = read_environment_file(filename, {"DB_PORT": "6543"})
    assert values["DB_PORT"] == "6543" and values["DB_HOST"] == "127.0.0.1"
    assert values["DB_APP_PASSWORD"] == "literal$(never-run)#value"
    assert load_settings(values).database is None


@pytest.mark.parametrize("content", ["BAD LINE", "KEY=one\nKEY=two", "KEY='unterminated", "!KEY=secret"])
def test_env_file_errors_do_not_echo_secrets(tmp_path, content):
    filename = tmp_path / "bad.env"
    filename.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigurationError) as caught:
        read_environment_file(filename, {})
    assert "secret" not in str(caught.value) and "unterminated" not in str(caught.value)


def test_cli_disabled_and_configuration_error(capsys, monkeypatch):
    monkeypatch.setenv("PERSISTENCE_MODE", "legacy_json")
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["database_checked"] is False
    monkeypatch.setenv("PERSISTENCE_MODE", "unsafe-value")
    assert main([]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["code"] == "DB_CONFIGURATION_INVALID" and "unsafe-value" not in str(result)


def test_disabled_modules_import_without_db_packages_or_connecting():
    script = """
import sys, importlib.abc, socket
class NoDatabasePackages(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'sqlalchemy', 'psycopg'}:
            raise ModuleNotFoundError('DB package deliberately unavailable')
sys.meta_path.insert(0, NoDatabasePackages())
def no_connection(*args, **kwargs):
    raise AssertionError('import/disabled mode must not connect')
socket.socket = no_connection
from backend.app.config import load_settings
from backend.app.persistence.database import create_app_engine, PersistenceError
from backend.app.persistence.transaction import unit_of_work
assert load_settings({}).database is None
try:
    create_app_engine(load_settings({}))
except PersistenceError as error:
    assert error.code == 'PERSISTENCE_DISABLED'
else:
    raise AssertionError('disabled Engine creation must fail')
try:
    create_app_engine(load_settings({'PERSISTENCE_MODE': 'postgres', 'DB_HOST': '127.0.0.1',
        'POSTGRES_DB': 'test', 'DB_APP_USER': 'app', 'DB_APP_PASSWORD': 'private'}))
except PersistenceError as error:
    assert error.code == 'DB_DEPENDENCIES_MISSING'
else:
    raise AssertionError('explicit DB mode must not fall back to JSON')
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_selected_integration_run_requires_explicit_test_configuration():
    import os
    environment = {k: v for k, v in os.environ.items() if not k.startswith("TEST_DB_")}
    result = subprocess.run([sys.executable, "-m", "pytest", "backend/tests/test_db_integration.py::test_connection_identity_limits_and_release",
                             "-m", "db_integration", "-q"], env=environment, capture_output=True, text=True)
    assert result.returncode == 1
    assert "TEST_DB_ALLOW_CREATE=1" in result.stdout and "1 error" in result.stdout
