"""Batch checks validate teammate contracts without claiming real connectivity."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import types

import pytest

from backend.agent_framework.adapters.generate_examples import role_examples
from backend.agent_framework.adapters.synthetic import make_agents
from backend.agent_framework.checks import load_suite, run_checks
from backend.agent_framework.cli import main
from backend.agent_framework.composition import ROLE_PORTS, RoleBinding, TeamConfiguration
from backend.agent_framework.ports import AdapterFailure


def suite_file(tmp_path, role="A02", cases=None):
    """Use parent-relative portable fixtures, independent from the process cwd."""
    root = tmp_path / "공유 예제"
    inputs, suites = root / "inputs", root / "checks"
    inputs.mkdir(parents=True, exist_ok=True)
    suites.mkdir(exist_ok=True)
    if cases is None:
        cases = [("normal", role_examples()[role][0], {})]
    specs = []
    for case_id, request, options in cases:
        path = inputs / f"{case_id}.json"
        value = request.model_dump(mode="json") if hasattr(request, "model_dump") else request
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        specs.append({"id": case_id, "input": f"../inputs/{case_id}.json", **options})
    path = suites / f"{role}.suite.json"
    path.write_text(json.dumps({"contract_version": "2.0.0", "role": role, "cases": specs}), encoding="utf-8")
    return path


def config_file(tmp_path, config):
    path = tmp_path / "team config.json"
    path.write_text(config.model_dump_json(), encoding="utf-8")
    return path


def synthetic_config():
    return TeamConfiguration(roles={
        role: RoleBinding(kind="reference" if role == "A06" else "synthetic")
        for role in ROLE_PORTS
    })


def selected_config(monkeypatch, role, adapter):
    calls = []
    module = types.ModuleType("framework_batch_test_adapter")

    def create():
        calls.append(role)
        return adapter

    module.create = create
    monkeypatch.setitem(sys.modules, module.__name__, module)
    config = TeamConfiguration(roles={key: RoleBinding(kind="factory", factory=(
        "framework_batch_test_adapter:create" if key == role else "batch_unselected_module_must_not_import:create"))
        for key in ROLE_PORTS})
    return config, calls


def invoke_cli(path, role, configuration, *extra):
    return main(["check", role, "--cases", str(path), "--config", str(configuration), *map(str, extra)])


def test_suite_is_portable_bom_aware_and_initializes_only_selected_role(tmp_path, monkeypatch):
    request, response = role_examples()["A02"]
    request = request.model_copy(update={"document": request.document.model_copy(
        update={"file_id": "private-input-token"})})
    response = response.model_copy(update={"warnings": ("private-output-token",)})
    received = []
    adapter = types.SimpleNamespace(extract=lambda value: received.append(value) or response)
    config, factories = selected_config(monkeypatch, "A02", adapter)
    path = suite_file(tmp_path, cases=[("one", request, {}), ("two", request, {})])
    # BOMs from Windows editors are valid in both the suite and requests.
    path.write_text(path.read_text(), encoding="utf-8-sig")
    first_input = path.parent / "../inputs/one.json"
    first_input.write_text(first_input.read_text(), encoding="utf-8-sig")
    monkeypatch.chdir(tmp_path)
    prepared = load_suite(path, "A02")
    assert factories == []
    assert len(prepared.cases) == 2
    report = run_checks(prepared, config)
    assert factories == ["A02"] and len(received) == 2
    assert "batch_unselected_module_must_not_import" not in sys.modules
    assert report["check"] == "role_contract_suite" and report["passed"]
    assert report["connection_verified"] is False
    assert report["summary"] == {"total": 2, "passed": 2, "failed": 0, "skipped": 0, "contract_outputs_checked": 2}
    for item in report["cases"]:
        assert item["synthetic"] is True
        assert re.fullmatch(r"[a-f0-9]{64}", item["request_sha256"])
        assert re.fullmatch(r"[a-f0-9]{64}", item["output_sha256"])
    rendered = json.dumps(report)
    assert "private-input-token" not in rendered and "private-output-token" not in rendered


@pytest.mark.parametrize("role", ("A06", "A07"))
def test_fail_and_review_are_valid_contract_results_not_check_failures(tmp_path, role):
    cases = [(scenario, role_examples(scenario)[role][0], {"expected_verdict": scenario})
             for scenario in ("pass", "fail", "review")]
    report = run_checks(load_suite(suite_file(tmp_path, role, cases), role), synthetic_config())
    assert report["passed"]
    assert report["summary"]["contract_outputs_checked"] == 3
    assert [case["machine_verdict"] for case in report["cases"]] == ["pass", "fail", "review"]


def test_verdict_mismatch_is_failed_check_but_still_records_valid_output(tmp_path):
    request = role_examples("fail")["A06"][0]
    path = suite_file(tmp_path, "A06", [("mismatch", request, {"expected_verdict": "pass"})])
    report = run_checks(load_suite(path, "A06"), synthetic_config())
    assert not report["passed"]
    assert report["summary"]["failed"] == 1
    assert report["summary"]["contract_outputs_checked"] == 1
    assert report["cases"][0]["machine_verdict"] == "fail"


def test_expected_boundary_rejection_never_calls_adapter_and_is_not_output_verification(tmp_path, monkeypatch):
    request, response = role_examples()["A02"]
    invalid = request.model_copy(update={"document": request.document.model_copy(
        update={"document_version_id": "other-document"})})
    calls = []
    adapter = types.SimpleNamespace(extract=lambda value: calls.append(value) or response)
    config, factories = selected_config(monkeypatch, "A02", adapter)
    path = suite_file(tmp_path, cases=[("wrong-version", invalid, {"expected_error": "DOCUMENT_VERSION_MISMATCH"})])
    report = run_checks(load_suite(path, "A02"), config)
    assert factories == ["A02"] and calls == []
    assert report["passed"] and report["cases"][0]["status"] == "passed"
    assert report["cases"][0]["error"]["code"] == "DOCUMENT_VERSION_MISMATCH"
    assert report["summary"]["contract_outputs_checked"] == 0


@pytest.mark.parametrize("expected,should_pass", (("REMOTE_UNAVAILABLE", True), ("DIFFERENT_ERROR", False), (None, False)))
def test_known_adapter_error_matches_only_explicit_code_and_never_retries(tmp_path, monkeypatch, expected, should_pass):
    request = role_examples()["A02"][0]
    calls = []

    def fail(value):
        calls.append(value)
        raise AdapterFailure("REMOTE_UNAVAILABLE", "private-provider-message", retryable=True)

    config, _ = selected_config(monkeypatch, "A02", types.SimpleNamespace(extract=fail))
    options = {"expected_error": expected} if expected else {}
    path = suite_file(tmp_path, cases=[("unavailable", request, options)])
    report = run_checks(load_suite(path, "A02"), config)
    assert report["passed"] is should_pass
    assert len(calls) == 1 and report["summary"]["contract_outputs_checked"] == 0
    assert report["cases"][0]["error"]["retryable"] is True
    assert "private-provider-message" not in json.dumps(report)


def test_outcome_unknown_always_fails_and_skips_remaining_cases_even_if_expected(tmp_path, monkeypatch):
    request = role_examples()["A02"][0]
    calls = []

    def ambiguous(value):
        calls.append(value)
        raise AdapterFailure("REMOTE_UNKNOWN", "private-response", retryable=True, outcome_unknown=True)

    config, _ = selected_config(monkeypatch, "A02", types.SimpleNamespace(extract=ambiguous))
    path = suite_file(tmp_path, cases=[("unknown", request, {"expected_error": "REMOTE_UNKNOWN"}),
                                      ("later", request, {})])
    report = run_checks(load_suite(path, "A02"), config)
    assert not report["passed"] and len(calls) == 1
    assert [case["status"] for case in report["cases"]] == ["failed", "skipped"]
    assert report["cases"][0]["error"]["outcome_unknown"] is True
    assert report["summary"] == {"total": 2, "passed": 0, "failed": 1, "skipped": 1, "contract_outputs_checked": 0}
    assert "private-response" not in json.dumps(report)


def test_unexpected_error_is_sanitized_and_output_contract_is_still_checked(tmp_path, monkeypatch):
    request, response = role_examples()["A02"]
    calls = []

    def provider(value):
        calls.append(value)
        if len(calls) == 1:
            raise RuntimeError("private-token-never-print")
        return response.model_copy(update={"criteria": ()})

    config, _ = selected_config(monkeypatch, "A02", types.SimpleNamespace(extract=provider))
    path = suite_file(tmp_path, cases=[("exception", request, {}), ("bad-output", request, {})])
    report = run_checks(load_suite(path, "A02"), config)
    assert not report["passed"] and len(calls) == 2
    assert [case["error"]["code"] for case in report["cases"]] == ["ADAPTER_FAILED", "CONTRACT_INVALID"]
    assert report["summary"]["contract_outputs_checked"] == 0
    assert "private-token" not in json.dumps(report)


@pytest.mark.parametrize("mutation", ("empty", "duplicate-id", "wrong-role", "both-expectations", "wrong-verdict-role",
                                      "absolute-posix", "absolute-windows", "backslash", "extra", "future-version"))
def test_malformed_suites_fail_before_any_factory_and_preserve_report(tmp_path, monkeypatch, capsys, mutation):
    path = suite_file(tmp_path)
    value = json.loads(path.read_text())
    case = value["cases"][0]
    if mutation == "empty": value["cases"] = []
    elif mutation == "duplicate-id": value["cases"].append(dict(case))
    elif mutation == "wrong-role": value["role"] = "A03"
    elif mutation == "both-expectations": case.update(expected_error="CONTRACT_INVALID", expected_verdict="pass")
    elif mutation == "wrong-verdict-role": case["expected_verdict"] = "pass"
    elif mutation == "absolute-posix": case["input"] = "/tmp/request.json"
    elif mutation == "absolute-windows": case["input"] = "C:/Temp/request.json"
    elif mutation == "backslash": case["input"] = "..\\inputs\\normal.json"
    elif mutation == "extra": value["factory"] = "private-token:create"
    else: value["contract_version"] = "3.0.0"
    path.write_text(json.dumps(value), encoding="utf-8")
    config, factories = selected_config(monkeypatch, "A02", make_agents().extractor)
    output = tmp_path / "report.json"
    output.write_text("previous", encoding="utf-8")
    assert invoke_cli(path, "A02", config_file(tmp_path, config), "--output", output) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "CHECK_SUITE_INVALID"
    assert factories == [] and output.read_text() == "previous"
    assert "private-token" not in captured.out + captured.err


@pytest.mark.parametrize("location,text", (("suite", '{"role":"A02","role":"A02","cases":[]}'),
    ("suite", '{"role":"A02","cases":[],"private-token":NaN}'),
    ("request", '{"synthetic":true,"synthetic":false}'),
    ("request", '{"page_count":Infinity}'), ("request", '{}')))
def test_all_json_and_request_schemas_are_preflighted_before_initialization(tmp_path, monkeypatch, capsys, location, text):
    request = role_examples()["A02"][0]
    path = suite_file(tmp_path, cases=[("first", request, {}), ("later", request, {})])
    target = path if location == "suite" else path.parent / "../inputs/later.json"
    target.write_text(text, encoding="utf-8")
    config, factories = selected_config(monkeypatch, "A02", make_agents().extractor)
    assert invoke_cli(path, "A02", config_file(tmp_path, config)) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == ("CHECK_SUITE_INVALID" if location == "suite" else "CONTRACT_INVALID")
    assert factories == []
    assert "private-token" not in captured.out + captured.err


def test_missing_fixture_is_file_error_before_initialization(tmp_path, monkeypatch, capsys):
    path = suite_file(tmp_path)
    (path.parent / "../inputs/normal.json").unlink()
    config, factories = selected_config(monkeypatch, "A02", make_agents().extractor)
    assert invoke_cli(path, "A02", config_file(tmp_path, config)) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "FILE_UNAVAILABLE"
    assert factories == []


@pytest.mark.parametrize("target", ("suite", "configuration", "fixture"))
def test_cli_report_cannot_overwrite_inputs_or_configuration(tmp_path, monkeypatch, capsys, target):
    path = suite_file(tmp_path)
    config, factories = selected_config(monkeypatch, "A02", make_agents().extractor)
    configuration = config_file(tmp_path, config)
    destinations = {"suite": path, "configuration": configuration, "fixture": path.parent / "../inputs/normal.json"}
    destination = destinations[target]
    original = destination.read_bytes()
    assert invoke_cli(path, "A02", configuration, "--output", destination) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_ARGUMENT"
    assert destination.read_bytes() == original and factories == []


def test_cli_writes_failed_comparison_report_with_exit_one(tmp_path, capsys):
    request = role_examples("review")["A06"][0]
    path = suite_file(tmp_path, "A06", [("review", request, {"expected_verdict": "pass"})])
    output = tmp_path / "report.json"
    assert invoke_cli(path, "A06", config_file(tmp_path, synthetic_config()), "--output", output) == 1
    assert capsys.readouterr().out == ""
    report = json.loads(output.read_text())
    assert not report["passed"] and report["cases"][0]["machine_verdict"] == "review"


def test_unconfigured_role_cannot_become_synthetic_success(tmp_path, capsys):
    path = suite_file(tmp_path)
    config = synthetic_config()
    config = config.model_copy(update={"roles": {**config.roles, "A02": RoleBinding(kind="unconfigured")}})
    assert invoke_cli(path, "A02", config_file(tmp_path, config)) == 1
    report = json.loads(capsys.readouterr().out)
    assert not report["passed"]
    assert report["cases"][0]["error"]["code"] == "ADAPTER_NOT_CONFIGURED"
    assert report["summary"]["contract_outputs_checked"] == 0


def test_untyped_provider_exception_cannot_pass_an_expected_generic_error(tmp_path, monkeypatch):
    request = role_examples()["A02"][0]

    def fail(_request):
        raise RuntimeError("private-token")

    config, _ = selected_config(monkeypatch, "A02", types.SimpleNamespace(extract=fail))
    path = suite_file(tmp_path, cases=[("generic", request, {"expected_error": "ADAPTER_FAILED"})])
    report = run_checks(load_suite(path, "A02"), config)
    assert not report["passed"]
    assert report["cases"][0]["error"]["code"] == "ADAPTER_FAILED"
    assert report["cases"][0]["outcome"] != "expected_rejection"
    assert "private-token" not in json.dumps(report)


def test_factory_initialization_error_preserves_existing_report(tmp_path, monkeypatch, capsys):
    path = suite_file(tmp_path)
    config, factories = selected_config(monkeypatch, "A02", make_agents().extractor)

    def bad_factory():
        factories.append("failed")
        raise RuntimeError("private-factory-token")

    monkeypatch.setattr(sys.modules["framework_batch_test_adapter"], "create", bad_factory)
    output = tmp_path / "report.json"
    output.write_text("previous", encoding="utf-8")
    assert invoke_cli(path, "A02", config_file(tmp_path, config), "--output", output) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "ADAPTER_FACTORY_FAILED"
    assert factories == ["failed"] and output.read_text() == "previous"
    assert "private-factory-token" not in captured.out + captured.err


@pytest.mark.parametrize("role,count", (("A02", 2), ("A04_A05", 2), ("A06", 5), ("A07", 6)))
def test_shipped_suites_pass_against_explicit_synthetic_composition(role, count):
    path = Path(__file__).resolve().parents[2] / "examples" / "agent_framework" / "checks" / f"{role}.suite.json"
    report = run_checks(load_suite(path, role), synthetic_config())
    assert report["passed"], report
    assert report["summary"]["total"] == count
    assert report["summary"]["skipped"] == report["summary"]["failed"] == 0
    assert report["connection_verified"] is False


@pytest.mark.parametrize("code,expected,reported,should_pass", (
    ("remote_timeout", "remote_timeout", "remote_timeout", True),
    ("private-token:never-print", "ADAPTER_FAILED", "ADAPTER_FAILED", False),
))
def test_existing_lowercase_errors_match_but_malformed_codes_are_sanitized(
        tmp_path, monkeypatch, code, expected, reported, should_pass):
    request = role_examples()["A02"][0]
    calls = []

    def fail(value):
        calls.append(value)
        raise AdapterFailure(code, "private-provider-message", retryable=True)

    config, _ = selected_config(monkeypatch, "A02", types.SimpleNamespace(extract=fail))
    path = suite_file(tmp_path, cases=[("provider-error", request, {"expected_error": expected})])
    report = run_checks(load_suite(path, "A02"), config)
    assert report["passed"] is should_pass
    assert len(calls) == 1
    assert report["cases"][0]["error"]["code"] == reported
    assert report["cases"][0]["error"]["retryable"] is True
    assert report["summary"]["contract_outputs_checked"] == 0
    rendered = json.dumps(report)
    assert "private-token" not in rendered and "private-provider-message" not in rendered
