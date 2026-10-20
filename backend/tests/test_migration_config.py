"""Migration settings remain explicit and separate from app credentials."""
import pytest

from backend.app.config import ConfigurationError
from backend.migrations.configuration import load_migration_settings


def owner_environment(**changes):
    return {"DB_HOST": "127.0.0.1", "DB_PORT": "5432", "POSTGRES_DB": "test_database",
            "DB_OWNER_USER": "test_owner", "DB_OWNER_PASSWORD": "owner-@:/?#%$-unit-secret",
            "DB_APP_USER": "test_app", **changes}


def test_owner_password_is_literal_and_masked_without_app_password():
    values = owner_environment()
    settings = load_migration_settings(values)
    assert settings.owner_password.reveal() == values["DB_OWNER_PASSWORD"]
    assert values["DB_OWNER_PASSWORD"] not in repr(settings)
    assert not hasattr(settings, "app_password")


@pytest.mark.parametrize("changes", [
    {"DB_HOST": ""}, {"DB_HOST": "postgresql://localhost"}, {"DB_PORT": "0"},
    {"DB_PORT": "65536"}, {"DB_PORT": "bad"}, {"DB_PORT": ""},
    {"POSTGRES_DB": "unsafe;drop"}, {"DB_OWNER_USER": "postgres"},
    {"DB_OWNER_USER": "test_app"}, {"DB_APP_USER": ""},
    {"DB_OWNER_PASSWORD": ""}, {"DB_OWNER_PASSWORD": "change-me-owner-password"},
    {"DB_OWNER_PASSWORD": "do-not-echo\n"},
])
def test_invalid_owner_settings_are_rejected_safely(changes):
    with pytest.raises(ConfigurationError) as caught:
        load_migration_settings(owner_environment(**changes))
    assert "do-not-echo" not in str(caught.value) and "unsafe;drop" not in str(caught.value)


def test_explicit_file_and_process_override(tmp_path):
    filename = tmp_path / "owner.env"
    values = owner_environment()
    filename.write_text("\n".join(f"{k}='{v}'" for k,v in values.items()), encoding="utf-8")
    settings = load_migration_settings({"CC_MIGRATION_ENV_FILE": str(filename), "POSTGRES_DB": "overridden_test"})
    assert settings.database == "overridden_test" and settings.owner_user == "test_owner"
