"""Configured six-action rehearsals, with no external models or services."""
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.agent_framework.adapters.synthetic import make_agents, sample_inputs
from backend.agent_framework.cli import main
from backend.agent_framework.composition import ROLE_PORTS, RoleBinding, TeamConfiguration
from backend.agent_framework.flow_checks import FlowCheckCase, run_flow_check
from backend.agent_framework.ports import AdapterFailure


def case(**changes):
    context, document, asset = sample_inputs()
    revised, _, revised_asset = sample_inputs("child-run", "poster-v2", context.run_id)
    values = dict(id="flow-case", context=context, document=document, asset=asset,
        revision_context=revised, revision_asset=revised_asset, expected_initial_verdict="pass",
        change_reason="Scripted fixture revision approval.")
    return FlowCheckCase(**{**values, **changes})


def config():
    return TeamConfiguration(roles={role: RoleBinding(kind="reference" if role == "A06" else "synthetic")
                                    for role in ROLE_PORTS})


def factories(monkeypatch, *, fail_role=None, error=None, transform=None):
    module = ModuleType("configured_flow_test_factories")
    initialized, invoked = [], []
    agents = make_agents()
    for role, (port, method) in ROLE_PORTS.items():
        original = getattr(getattr(agents, port), method)

        def call(request, role=role, original=original):
            invoked.append((request.context.run_id, role))
            if role == fail_role:
                raise error
            value = original(request)
            return transform(role, request, value) if transform else value

        adapter = SimpleNamespace(**{method: call})

        def create(role=role, adapter=adapter):
            initialized.append(role)
            return adapter

        setattr(module, "create_" + role, create)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    configuration = TeamConfiguration(roles={role: RoleBinding(kind="factory",
        factory=f"{module.__name__}:create_{role}") for role in ROLE_PORTS})
    return configuration, initialized, invoked


def write_inputs(tmp_path, fixture=None, configuration=None):
    root = tmp_path / "공유 예제 폴더"
    root.mkdir()
    inputs = root / "workflow.json"
    settings = root / "team.json"
    inputs.write_text((fixture or case()).model_dump_json(), encoding="utf-8-sig")
    settings.write_text((configuration or config()).model_dump_json(), encoding="utf-8-sig")
    return inputs, settings


def command(inputs, settings, *extra):
    return main(["check-flow", "--case", str(inputs), "--config", str(settings), *map(str, extra)])


def test_configured_roles_initialized_once_and_run_both_versions_through_all_boundaries(monkeypatch):
    configuration, initialized, invoked = factories(monkeypatch)
    report = run_flow_check(case(), configuration)
    assert initialized == list(ROLE_PORTS)
    assert invoked == [("demo-run-1", role) for role in ROLE_PORTS] + [
        ("child-run", role) for role in list(ROLE_PORTS)[1:]]
    assert report["passed"] and report["summary"] == {"total": 6, "passed": 6, "failed": 0, "skipped": 0}
    assert report["connection_verified"] is False and report["scripted_approvals"] is True
    assert report["storage"] == "memory" and report["production_ready"] is False
    assert report["actions"][3]["records"][0]["status"] == "reused"
    assert report["actions"][3]["records"][0]["source_run_id"] == "demo-run-1"
    assert report["actions"][-1]["human_decision"] == "final_approved"
    assert all(a["synthetic"] for a in report["actions"])
    assert len(report["case_sha256"]) == len(report["configuration_sha256"]) == 64
    assert all(len(a["snapshot_sha256"]) == 64 for a in report["actions"])


@pytest.mark.parametrize("failed_role,passed", [("A02", 0), ("A04_A05", 1), ("A06", 1), ("A07", 1)])
def test_each_role_failure_stops_the_flow_without_retry_or_approval_after_failure(monkeypatch, failed_role, passed):
    cfg, _, invoked = factories(monkeypatch, fail_role=failed_role,
        error=AdapterFailure("FILE_HASH_MISMATCH", "private-provider-message", retryable=True))
    report = run_flow_check(case(), cfg)
    assert not report["passed"] and report["summary"]["passed"] == passed
    assert report["summary"]["failed"] == 1 and report["summary"]["skipped"] == 5 - passed
    failed = report["actions"][passed]
    assert failed["reason"] == "STAGE_FAILED" and failed["error"]["code"] == "FILE_HASH_MISMATCH"
    assert invoked == [("demo-run-1", role) for role in list(ROLE_PORTS)[:list(ROLE_PORTS).index(failed_role) + 1]]
    assert "private-provider-message" not in json.dumps(report)


def test_unknown_outcome_stops_all_later_commands_and_does_not_count_as_expected_success(monkeypatch):
    cfg, _, invoked = factories(monkeypatch, fail_role="A04_A05",
        error=AdapterFailure("REMOTE_TIMEOUT", "unknown", outcome_unknown=True))
    report = run_flow_check(case(), cfg)
    assert not report["passed"] and report["stopped_on_outcome_unknown"]
    assert report["actions"][1]["execution_status"] == "outcome_unknown"
    assert len(invoked) == 2


@pytest.mark.parametrize("scenario", ["fail", "review"])
def test_valid_nonpass_initial_result_can_continue_to_a_corrected_version(monkeypatch, scenario):
    def transform(role, request, result):
        if request.context.parent_run_id is None:
            if role == "A04_A05":
                return make_agents(scenario).inspector.inspect(request)
        return result
    cfg, _, _ = factories(monkeypatch, transform=transform)
    report = run_flow_check(case(expected_initial_verdict="fail" if scenario == "fail" else "review"), cfg)
    assert report["passed"]
    assert report["actions"][-1]["machine_verdict"] == "pass"


def test_initial_verdict_mismatch_prevents_scripted_change_approval(monkeypatch):
    cfg, _, invoked = factories(monkeypatch)
    report = run_flow_check(case(expected_initial_verdict="fail"), cfg)
    assert report["summary"] == {"total": 6, "passed": 1, "failed": 1, "skipped": 4}
    assert report["actions"][1]["reason"] == "VERDICT_MISMATCH"
    assert len(invoked) == 4


def test_revised_nonpass_never_receives_final_approval(monkeypatch):
    cfg, _, _ = factories(monkeypatch, transform=lambda role, req, result:
        make_agents("fail").inspector.inspect(req) if role == "A04_A05" and req.context.parent_run_id else result)
    report = run_flow_check(case(), cfg)
    assert report["summary"] == {"total": 6, "passed": 4, "failed": 1, "skipped": 1}
    assert report["actions"][4]["reason"] == "VERDICT_MISMATCH"
    assert report["actions"][5]["status"] == "skipped"


@pytest.mark.parametrize("role", list(ROLE_PORTS)[1:])
def test_revised_stage_failure_preserves_initial_run_and_skips_final_approval(monkeypatch, role):
    def transform(current, request, result):
        if current == role and request.context.parent_run_id:
            raise AdapterFailure("REVISION_CALL_UNKNOWN", "private", outcome_unknown=True)
        return result
    cfg, _, invoked = factories(monkeypatch, transform=transform)
    report = run_flow_check(case(), cfg)
    assert report["summary"] == {"total": 6, "passed": 4, "failed": 1, "skipped": 1}
    assert report["stopped_on_outcome_unknown"]
    assert report["actions"][4]["records"][-1]["role"] == role
    assert report["actions"][4]["records"][0]["status"] == "reused"
    assert report["actions"][2]["human_decision"] == "changes_approved"
    assert all(run == "child-run" for run, _ in invoked[4:])


def test_dev_api_with_configured_factories_completes_the_same_six_actions(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.agent_framework.api import create_app
    from backend.agent_framework.contracts import Approval

    cfg, initialized, invoked = factories(monkeypatch)
    fixture = case()
    prefix = "/api/framework/v1/runs"
    with TestClient(create_app(configuration=cfg)) as client:
        first = client.post(prefix, json={key: getattr(fixture, key).model_dump(mode="json")
                                        for key in ("context", "document", "asset")})
        assert first.status_code == 201
        def approve(context, decision, **fields):
            response = client.post(prefix + "/" + context.run_id + "/approvals",
                json=Approval(context=context, actor_id=context.actor_id, decision=decision, **fields).model_dump(mode="json"))
            assert response.status_code == 200
            return response.json()
        assert approve(fixture.context, "approve_criteria")["execution_status"] == "waiting_review"
        parent = approve(fixture.context, "approve_changes", reason="Scripted API test revision")
        child = client.post(prefix + "/" + fixture.context.run_id + "/revisions", json={
            "context": fixture.revision_context.model_dump(mode="json"),
            "asset": fixture.revision_asset.model_dump(mode="json")})
        assert child.status_code == 201
        assert child.json()["step_records"][0]["status"] == "reused"
        assert approve(fixture.revision_context, "approve_criteria")["execution_status"] == "waiting_review"
        assert approve(fixture.revision_context, "approve_final")["human_decision"] == "final_approved"
        assert client.get(prefix + "/" + fixture.context.run_id).json() == parent
        records = client.get(prefix + "/" + fixture.revision_context.run_id + "/steps").json()["records"]
        assert len(records) == 4 and records[0]["status"] == "reused"
    assert initialized == list(ROLE_PORTS) and len(invoked) == 7


def test_report_contract_failure_retains_specific_stage_code(monkeypatch):
    cfg, _, _ = factories(monkeypatch, transform=lambda role, req, result:
        result.model_copy(update={"verdict": "fail"}) if role == "A07" else result)
    report = run_flow_check(case(), cfg)
    failed = report["actions"][1]
    assert failed["reason"] == "STAGE_FAILED"
    assert failed["records"][-1]["error"]["code"] != "contract_violation"


def test_report_omits_source_quotes_prompts_and_provider_output(monkeypatch):
    cfg, _, _ = factories(monkeypatch, transform=lambda role, req, result:
        result.model_copy(update={"warnings": ("private-output-token",)}) if role == "A02" else result)
    report = run_flow_check(case(change_reason="private-approval-token"), cfg)
    rendered = json.dumps(report, ensure_ascii=False)
    assert "private-output-token" not in rendered and "private-approval-token" not in rendered
    assert "포스터에는" not in rendered


@pytest.mark.parametrize("field,value", [("change_reason", " "), ("expected_revised_verdict", "fail"),
    ("revision_context", sample_inputs()[0]), ("revision_asset", sample_inputs()[2])])
def test_invalid_preflight_cannot_import_factories_even_with_bypassed_models(monkeypatch, field, value):
    cfg, initialized, invoked = factories(monkeypatch)
    invalid = case().model_copy(update={field: value})
    with pytest.raises(ValidationError):
        run_flow_check(invalid, cfg)
    assert initialized == invoked == []


def test_cli_is_portable_bom_aware_and_writes_readable_report(tmp_path, monkeypatch, capsys):
    inputs, settings = write_inputs(tmp_path)
    output = tmp_path / "보고서.json"
    monkeypatch.chdir(tmp_path)
    assert command(inputs, settings, "--output", output) == 0
    report = json.loads(output.read_text())
    assert report["passed"] and report["summary"]["passed"] == 6
    assert "scripted" in capsys.readouterr().err


def test_cli_saves_failed_report_with_exit_one(tmp_path):
    inputs, settings = write_inputs(tmp_path, case(expected_initial_verdict="fail"))
    output = tmp_path / "failure.json"
    assert command(inputs, settings, "--output", output) == 1
    assert not json.loads(output.read_text())["passed"]


@pytest.mark.parametrize("raw", ['{"id":"a","id":"b"}', '{"expected_initial_verdict":NaN}', '{}', '{'])
def test_malformed_cases_preserve_previous_report_and_do_not_import_factories(tmp_path, monkeypatch, raw, capsys):
    cfg, initialized, invoked = factories(monkeypatch)
    inputs, settings = write_inputs(tmp_path, configuration=cfg)
    inputs.write_text(raw)
    output = tmp_path / "report.json"
    output.write_text("prior-report")
    assert command(inputs, settings, "--output", output) == 2
    assert output.read_text() == "prior-report" and initialized == invoked == []
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "FLOW_CASE_INVALID"


@pytest.mark.parametrize("target", ["case", "config"])
def test_report_cannot_overwrite_its_inputs(tmp_path, monkeypatch, target, capsys):
    cfg, initialized, invoked = factories(monkeypatch)
    inputs, settings = write_inputs(tmp_path, configuration=cfg)
    output = inputs if target == "case" else settings
    before = output.read_bytes()
    assert command(inputs, settings, "--output", output) == 2
    assert output.read_bytes() == before and initialized == invoked == []


def test_unconfigured_roles_do_not_fall_back_to_synthetic():
    cfg = config()
    cfg.roles["A02"] = RoleBinding(kind="unconfigured")
    report = run_flow_check(case(), cfg)
    assert not report["passed"] and report["actions"][0]["error"]["code"] == "ADAPTER_NOT_CONFIGURED"


def test_invalid_output_parent_is_rejected_before_factories_are_called(tmp_path, monkeypatch):
    cfg, initialized, invoked = factories(monkeypatch)
    inputs, settings = write_inputs(tmp_path, configuration=cfg)
    assert command(inputs, settings, "--output", tmp_path / "missing" / "report.json") == 2
    assert initialized == invoked == []


def test_factory_initialization_failure_keeps_previous_report(tmp_path, capsys):
    cfg = config()
    cfg.roles["A07"] = RoleBinding(kind="factory", factory="missing_workflow_module:create")
    inputs, settings = write_inputs(tmp_path, configuration=cfg)
    output = tmp_path / "previous.json"
    output.write_text("previous")
    assert command(inputs, settings, "--output", output) == 1
    assert output.read_text() == "previous"
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "ADAPTER_FACTORY_FAILED"


def test_generated_files_use_existing_storage_and_run_fail_revision_pass():
    from examples.agent_framework.file_flow_demo import run_demo
    result = run_demo()
    assert result["generated_test_media"] and result["model_calls"] == 0
    assert result["database"] == "isolated SQLite" and not result["operational_database_checked"]
    report = result["workflow"]
    assert report["passed"] and report["summary"]["passed"] == 6
    assert report["actions"][1]["machine_verdict"] == "fail"
    assert report["actions"][4]["machine_verdict"] == "pass"
    assert report["actions"][3]["records"][0]["status"] == "reused"
