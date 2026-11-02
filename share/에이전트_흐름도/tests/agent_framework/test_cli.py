"""Developer entry points use real contracts and explicit synthetic adapters."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest

from backend.agent_framework.cli import main
from backend.agent_framework.contracts import CONTRACT_TYPES, ExtractRequest
from backend.agent_framework.adapters.synthetic import make_agents, sample_inputs
from backend.agent_framework.adapters.templates import QwenAdapterStub


def input_file(tmp_path):
    context, document, _asset = sample_inputs()
    path = tmp_path / "request.json"
    path.write_text(ExtractRequest(context=context, document=document).model_dump_json(), encoding="utf-8")
    return path


def register_factory(monkeypatch, factory):
    module = types.ModuleType("framework_test_factory")
    module.make = factory
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return "framework_test_factory:make"


def test_role_requires_explicit_adapter(tmp_path, capsys):
    result = main(["role", "A02", "--input", str(input_file(tmp_path))])
    assert result == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "ADAPTER_NOT_CONFIGURED"


def test_synthetic_role_has_label_and_is_valid_json(tmp_path, capsys):
    assert main(["role", "A02", "--input", str(input_file(tmp_path)), "--synthetic"]) == 0
    captured = capsys.readouterr()
    assert "SYNTHETIC" in captured.err
    assert json.loads(captured.out)["synthetic"] is True


def test_role_output_file_does_not_mix_logs_into_json(tmp_path, capsys):
    output = tmp_path / "response.json"
    assert main(["role", "A02", "--input", str(input_file(tmp_path)), "--synthetic", "--output", str(output)]) == 0
    assert capsys.readouterr().out == ""
    assert json.loads(output.read_text())["criteria"]


def test_invalid_json_is_safe_contract_error_and_preserves_output(tmp_path, capsys):
    request = tmp_path / "bad.json"
    request.write_text('{"secret": "do-not-print",', encoding="utf-8")
    output = tmp_path / "previous.json"
    output.write_text("previous", encoding="utf-8")
    assert main(["role", "A02", "--input", str(request), "--synthetic", "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert "do-not-print" not in captured.out + captured.err
    assert json.loads(captured.out)["error"]["code"] == "CONTRACT_INVALID"
    assert output.read_text() == "previous"


def test_output_write_failure_preserves_previous_file(tmp_path, capsys, monkeypatch):
    output = tmp_path / "previous.json"
    output.write_text("previous", encoding="utf-8")

    def reject_replace(*_):
        raise PermissionError("not writable")

    monkeypatch.setattr("backend.agent_framework.cli.os.replace", reject_replace)
    assert main(["role", "A02", "--input", str(input_file(tmp_path)), "--synthetic", "--output", str(output)]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "FILE_UNAVAILABLE"
    assert output.read_text() == "previous"
    assert not list(tmp_path.glob(".framework-*.tmp"))


def test_explicit_factory_can_supply_agents(tmp_path, capsys, monkeypatch):
    reference = register_factory(monkeypatch, make_agents)
    assert main(["role", "A02", "--input", str(input_file(tmp_path)), "--factory", reference]) == 0
    captured = capsys.readouterr()
    assert "EXPLICIT DEVELOPER FACTORY" in captured.err
    assert json.loads(captured.out)["context"]["run_id"] == "demo-run-1"


def test_unconfigured_stub_never_falls_back_to_fixture(tmp_path, capsys, monkeypatch):
    reference = register_factory(monkeypatch, lambda: replace(make_agents(), extractor=QwenAdapterStub()))
    assert main(["role", "A02", "--input", str(input_file(tmp_path)), "--factory", reference]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"]["code"] == "ADAPTER_NOT_CONFIGURED"
    assert "criteria" not in output


def test_factory_return_type_is_checked(tmp_path, capsys, monkeypatch):
    reference = register_factory(monkeypatch, lambda: {})
    assert main(["role", "A02", "--input", str(input_file(tmp_path)), "--factory", reference]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_FACTORY"


def test_request_json_cannot_inject_factory_or_execute_it(tmp_path, capsys, monkeypatch):
    called = []
    register_factory(monkeypatch, lambda: called.append(True))
    path = input_file(tmp_path)
    data = json.loads(path.read_text())
    data["factory"] = "framework_test_factory:make"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert main(["role", "A02", "--input", str(path), "--synthetic"]) == 2
    assert not called
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CONTRACT_INVALID"


def test_adapter_cannot_switch_run_scope(tmp_path, capsys, monkeypatch):
    class WrongScope:
        def extract(self, request):
            draft = make_agents().extractor.extract(request)
            return draft.model_copy(update={"context": draft.context.model_copy(update={"run_id": "other-run"})})

    reference = register_factory(monkeypatch, lambda: replace(make_agents(), extractor=WrongScope()))
    assert main(["role", "A02", "--input", str(input_file(tmp_path)), "--factory", reference]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CONTEXT_MISMATCH"


def test_unknown_exception_does_not_expose_adapter_secret(tmp_path, capsys, monkeypatch):
    def fail():
        raise RuntimeError("private-token-do-not-print")

    reference = register_factory(monkeypatch, fail)
    assert main(["role", "A02", "--input", str(input_file(tmp_path)), "--factory", reference]) == 1
    captured = capsys.readouterr()
    assert "private-token" not in captured.out + captured.err
    assert json.loads(captured.out)["error"]["code"] == "ADAPTER_FAILED"


@pytest.mark.parametrize("role,port,method", (("A02", "extractor", "extract"), ("A04_A05", "inspector", "inspect"),
    ("A06", "validator", "validate"), ("A07", "reporter", "report")))
def test_factory_cannot_remove_existing_synthetic_provenance(role, port, method, tmp_path, capsys, monkeypatch):
    from backend.agent_framework.adapters.generate_examples import role_examples

    request, response = role_examples()[role]
    path = tmp_path / "request.json"
    path.write_text(request.model_dump_json(), encoding="utf-8")
    adapter = types.SimpleNamespace(**{method: lambda _request: response.model_copy(update={"synthetic": False})})
    reference = register_factory(monkeypatch, lambda: replace(make_agents(), **{port: adapter}))
    assert main(["role", role, "--input", str(path), "--factory", reference]) == 0
    captured = capsys.readouterr()
    assert "SYNTHETIC INPUT" in captured.err
    assert json.loads(captured.out)["synthetic"] is True


def test_constructed_output_is_revalidated(tmp_path, capsys, monkeypatch):
    class InvalidDraft:
        def extract(self, request):
            return make_agents().extractor.extract(request).model_copy(update={"criteria": ()})

    reference = register_factory(monkeypatch, lambda: replace(make_agents(), extractor=InvalidDraft()))
    assert main(["role", "A02", "--input", str(input_file(tmp_path)), "--factory", reference]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CONTRACT_INVALID"


@pytest.mark.parametrize("role", ("A02", "A04_A05", "A06", "A07"))
def test_every_role_accepts_shared_request_fixture(role, tmp_path, capsys):
    from backend.agent_framework.adapters.generate_examples import role_examples

    request, expected = role_examples()[role]
    path = tmp_path / f"{role}.json"
    path.write_text(request.model_dump_json(), encoding="utf-8")
    assert main(["role", role, "--input", str(path), "--synthetic"]) == 0
    assert json.loads(capsys.readouterr().out) == expected.model_dump(mode="json")


def test_schema_export_matches_contract_types(tmp_path, capsys):
    assert main(["schema", "--output-dir", str(tmp_path / "schemas")]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["schema_count"] == len(CONTRACT_TYPES)
    for contract in CONTRACT_TYPES:
        actual = json.loads((tmp_path / "schemas" / f"{contract.__name__}.schema.json").read_text())
        assert actual == contract.model_json_schema()


def test_demo_covers_approval_changes_full_rereview_and_final(capsys):
    assert main(["demo"]) == 0
    captured = capsys.readouterr()
    demo = json.loads(captured.out)
    assert "SYNTHETIC" in captured.err
    assert demo["synthetic"] and demo["storage"] == "memory"
    assert [step["execution_status"] for step in demo["steps"]] == [
        "waiting_criteria", "waiting_review", "waiting_revision",
        "waiting_criteria", "waiting_review", "completed",
    ]
    old, new = demo["runs"]
    assert old["human_decision"] == "changes_approved"
    assert new["human_decision"] == "final_approved"
    assert new["context"]["parent_run_id"] == old["context"]["run_id"]
    assert new["context"]["asset_version_id"] != old["context"]["asset_version_id"]
    assert len(new["inspection"]["observations"]) == len(new["criteria"]["criteria"])


def test_python_module_entry_point_is_a_real_cli():
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run([sys.executable, "-m", "backend.agent_framework", "schema"],
        cwd=root, capture_output=True, text=True, check=False, timeout=20)
    assert completed.returncode == 0, completed.stderr
    assert set(json.loads(completed.stdout)) == {contract.__name__ for contract in CONTRACT_TYPES}
