"""Shared files retain role boundaries without connecting to a DB or a model."""
import os
from unittest.mock import Mock

import pytest

from backend.app.config import ConfigurationError
from backend.app.worker import load_worker_settings
from backend.runtime_environment import activate_runtime, runtime_values


def shared_file(tmp_path):
    path = tmp_path / "runtime.env"
    path.write_text("\n".join([
        "DATABASE_URL=postgresql+psycopg://app:synthetic@localhost/rebuild",
        "PERSISTENCE_MODE=postgres", "APP_ENV=test", "DB_HOST=localhost", "POSTGRES_DB=synthetic",
        "DB_APP_USER=app", "DB_APP_PASSWORD=synthetic-app", f"STORAGE_ROOT={tmp_path}",
        "CSRF_SECRET=browser-only", "CURSOR_SECRET=browser-only-2", "CC_MCP_TOKEN=tools-only",
        "CC_QWEN_API_KEY=model-only", "CC_NEO4J_PASSWORD=graph-only", "CC_COOKIE_SECURE=false",
        "UNRELATED_SECRET=not-a-service-setting",
    ]), encoding="utf-8")
    return path


@pytest.mark.parametrize("component,expected", [
    ("api", {"CC_NEO4J_PASSWORD"}), ("worker", {"CC_MCP_TOKEN"}),
    ("mcp", {"CC_MCP_TOKEN", "CC_QWEN_API_KEY"}), ("projection", {"CC_NEO4J_PASSWORD"}),
    ("v1", {"CSRF_SECRET", "CURSOR_SECRET"}), ("v1-worker", set()), ("framework-worker", {"CC_QWEN_API_KEY", "CC_NEO4J_PASSWORD"}),
    ("framework-projection", {"CC_NEO4J_PASSWORD"}),
])
def test_shared_file_selects_only_role_secrets(tmp_path, component, expected):
    values = runtime_values(component, shared_file(tmp_path), {})
    secrets = {"CSRF_SECRET", "CURSOR_SECRET", "CC_MCP_TOKEN", "CC_QWEN_API_KEY", "CC_NEO4J_PASSWORD"}
    assert values.keys() & secrets == expected
    assert "UNRELATED_SECRET" not in values


@pytest.mark.parametrize("key", ["DB_OWNER_PASSWORD", "POSTGRES_PASSWORD", "CC_LOCAL_DB_PASSWORD", "CC_LOCAL_GRAPH_PASSWORD"])
def test_admin_file_and_exported_secrets_are_rejected(tmp_path, key):
    path = tmp_path / "admin.env"
    path.write_text(f"{key}=do-not-print", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="owner/admin") as caught:
        runtime_values("api", path, {key: ""})
    assert "do-not-print" not in str(caught.value)
    with pytest.raises(ConfigurationError):
        runtime_values("worker", None, {key: "do-not-print"})


def test_test_file_cannot_be_selected_as_runtime(tmp_path):
    path = tmp_path / "test.env"
    path.write_text("TEST_DB_ALLOW_CREATE=1\nTEST_DB_ADMIN_PASSWORD=test-only", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="disposable-test"):
        runtime_values("v1", path, {})


def test_worker_defaults_do_not_hide_explicit_invalid_settings(tmp_path):
    path = shared_file(tmp_path)
    values = runtime_values("v1-worker", path, {})
    assert load_worker_settings(values).storage_root == tmp_path
    with pytest.raises(ConfigurationError):
        load_worker_settings(runtime_values("v1-worker", path, {"REVIEW_PIPELINE": "disabled"}))


def test_framework_worker_keeps_model_settings_for_the_real_adapter(tmp_path, monkeypatch):
    from backend.agent_framework.adapters.team_a04_a05 import ModelSettings

    path = shared_file(tmp_path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("\nCC_QWEN_BASE_URL=http://127.0.0.1:8001/v1\nA04_A05_MODEL_TIMEOUT=45\n")
    monkeypatch.setattr(os, "environ", {"A04_A05_MODEL_TIMEOUT": "60"})
    activate_runtime("framework-worker", path)
    settings = ModelSettings.from_environment()
    assert settings.base_url == "http://127.0.0.1:8001/v1"
    assert settings.api_key == "model-only" and settings.timeout == 60
    assert "CSRF_SECRET" not in os.environ and "CC_MCP_TOKEN" not in os.environ


def test_a02_key_is_kept_for_framework_worker_and_cleared_for_web(tmp_path, monkeypatch):
    path=shared_file(tmp_path)
    with path.open('a',encoding='utf-8') as stream:stream.write('\nA02_QWEN_API_KEY=synthetic-a02-only\n')
    assert runtime_values('framework-worker',path,{})['A02_QWEN_API_KEY']=='synthetic-a02-only'
    assert 'A02_QWEN_API_KEY' not in runtime_values('v1',path,{})
    monkeypatch.setattr(os,'environ',{'A02_QWEN_API_KEY':'previous-a02-only'})
    activate_runtime('v1',path)
    assert 'A02_QWEN_API_KEY' not in os.environ


def test_activation_clears_other_role_values_and_preserves_system_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", {"PATH": "system-path", "CSRF_SECRET": "browser-only", "TEST_DB_ADMIN_PASSWORD": "ci-only"})
    activate_runtime("worker", shared_file(tmp_path))
    assert os.environ["PATH"] == "system-path"
    assert "CSRF_SECRET" not in os.environ and "TEST_DB_ADMIN_PASSWORD" not in os.environ
    assert "CC_QWEN_API_KEY" not in os.environ


def test_rebuild_launcher_selects_file_before_loading_settings(tmp_path, monkeypatch):
    from backend.creative_checker.settings import get_settings
    from scripts.run_rebuild import main
    import uvicorn

    monkeypatch.setattr(os, "environ", {})
    serve = Mock()
    monkeypatch.setattr(uvicorn, "run", serve)
    try:
        main(["api", "--env-file", str(shared_file(tmp_path))])
        assert get_settings().database_url.endswith("/rebuild")
        assert not get_settings().cookie_secure
        assert get_settings().qwen_api_key == ""
        serve.assert_called_once()
    finally:
        get_settings.cache_clear()
