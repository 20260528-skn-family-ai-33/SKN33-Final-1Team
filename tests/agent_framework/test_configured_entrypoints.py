"""The same explicit team configuration drives isolated roles and the dev API."""
import json
from pathlib import Path
import sys
import types

from fastapi.testclient import TestClient
import pytest

from backend.agent_framework.api import create_app
from backend.agent_framework.adapters.synthetic import make_agents, sample_inputs
from backend.agent_framework.cli import main
from backend.agent_framework.composition import RoleBinding, TeamConfiguration, ROLE_PORTS
from backend.agent_framework.contracts import ExtractRequest

PREFIX = "/api/framework/v1"


def config(**replacements):
    roles = {role: RoleBinding(kind="reference" if role == "A06" else "synthetic") for role in ROLE_PORTS}
    roles.update(replacements)
    return TeamConfiguration(roles=roles)


def config_file(tmp_path, value):
    path = tmp_path / "team config.json"
    path.write_text(value.model_dump_json(), encoding="utf-8")
    return path


def request_file(tmp_path):
    context, document, _asset = sample_inputs()
    path = tmp_path / "request.json"
    path.write_text(ExtractRequest(context=context, document=document).model_dump_json(), encoding="utf-8")
    return path


def test_doctor_does_not_import_missing_factories_or_claim_connectivity(tmp_path, capsys):
    team = config(A07=RoleBinding(kind="factory", factory="does_not_exist_here:create_reporter"))
    assert main(["doctor", "--config", str(config_file(tmp_path, team))]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["factory_imports_performed"] is False
    assert result["role_calls_performed"] is False
    assert result["connection_verified"] is False
    assert result["roles"]["A07"]["kind"] == "factory"
    assert "does_not_exist_here" not in sys.modules


def test_role_config_initializes_only_requested_adapter(tmp_path, capsys):
    team = config(A07=RoleBinding(kind="factory", factory="does_not_exist_here:create_reporter"))
    assert main(["role", "A02", "--input", str(request_file(tmp_path)),
                 "--config", str(config_file(tmp_path, team))]) == 0
    assert json.loads(capsys.readouterr().out)["synthetic"] is True


def test_invalid_configuration_is_safe_and_does_not_replace_previous_output(tmp_path, capsys):
    path = tmp_path / "invalid.json"
    path.write_text('{"roles":{}, "roles":{}, "private-token":"never-print-me"}', encoding="utf-8")
    output = tmp_path / "previous.json"
    output.write_text("previous", encoding="utf-8")
    assert main(["doctor", "--config", str(path), "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "CONFIG_INVALID"
    assert "never-print-me" not in captured.out + captured.err
    assert output.read_text() == "previous"


def test_template_role_is_explicitly_unconfigured(tmp_path, capsys):
    team = config(A02=RoleBinding(kind="unconfigured"))
    assert main(["role", "A02", "--input", str(request_file(tmp_path)),
                 "--config", str(config_file(tmp_path, team))]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "ADAPTER_NOT_CONFIGURED"


def test_configuration_is_exclusive_with_synthetic_and_factory(tmp_path):
    with pytest.raises(SystemExit) as error:
        main(["role", "A02", "--input", str(request_file(tmp_path)), "--synthetic", "--config", "team.json"])
    assert error.value.code == 2


def test_mixed_config_api_reports_every_role_and_preserves_synthetic_through_approval(monkeypatch):
    calls = []
    class TeamReporter:
        def report(self, request):
            calls.append(request.context.run_id)
            # Test adapter intentionally tries to discard provenance.
            return make_agents().reporter.report(request).model_copy(update={"synthetic": False})
    module = types.ModuleType("configured_test_reporter")
    module.create = lambda: TeamReporter()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    team = config(A07=RoleBinding(kind="factory", factory="configured_test_reporter:create"))
    with TestClient(create_app(configuration=team)) as client:
        health = client.get(PREFIX + "/health").json()
        assert health["default_adapters"] == "mixed"
        assert health["roles"]["A02"]["kind"] == "synthetic"
        assert health["roles"]["A07"]["kind"] == "custom"
        status = client.get(PREFIX + "/configuration").json()
        assert status["declared"]["roles"]["A07"]["kind"] == "factory"
        assert status["initialized"]["connection_verified"] is False
        assert calls == []
        context, document, asset = sample_inputs()
        payload = {"context": context.model_dump(mode="json"), "document": document.model_dump(mode="json"),
                   "asset": asset.model_dump(mode="json")}
        assert client.post(PREFIX + "/runs", json=payload).status_code == 201
        run_url = PREFIX + "/runs/" + context.run_id
        approval = {"context": payload["context"], "actor_id": context.actor_id, "decision": "approve_criteria"}
        reviewed = client.post(run_url + "/approvals", json=approval).json()
        assert reviewed["execution_status"] == "waiting_review"
        assert reviewed["synthetic"] and reviewed["evidence"]["synthetic"]
        assert calls == [context.run_id]
        approval["decision"] = "approve_final"
        assert client.post(run_url + "/approvals", json=approval).json()["human_decision"] == "final_approved"
        payload["configuration"] = team.model_dump(mode="json")
        assert client.post(PREFIX + "/runs", json=payload).status_code == 422


def test_api_rejects_ambiguous_compositions_and_profile_override():
    with pytest.raises(ValueError):
        create_app(make_agents(), configuration=config())
    with pytest.raises(ValueError):
        create_app(profile="test", configuration=config())
    with pytest.raises(ValueError):
        create_app(profile="")


def test_serve_binds_only_loopback_and_does_not_start_any_run(tmp_path, monkeypatch):
    calls = []
    fake = types.ModuleType("uvicorn")
    fake.run = lambda app, **options: calls.append((app, options))
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    assert main(["serve", "--config", str(config_file(tmp_path, config())), "--port", "8123"]) == 0
    assert len(calls) == 1
    app, options = calls[0]
    assert options == {"host": "127.0.0.1", "port": 8123, "workers": 1, "reload": False}
    assert app.state.workflow._runs == {}


@pytest.mark.parametrize("port", ["0", "65536", "public"])
def test_serve_rejects_invalid_port(port):
    with pytest.raises(SystemExit) as error:
        main(["serve", "--config", "unused.json", "--port", port])
    assert error.value.code == 2


def test_optional_configuration_schemas_are_exported_without_changing_default(capsys):
    assert main(["schema", "--include-configuration"]) == 0
    schemas = json.loads(capsys.readouterr().out)
    assert schemas["TeamConfiguration"] == TeamConfiguration.model_json_schema()
    assert schemas["RoleBinding"] == RoleBinding.model_json_schema()
