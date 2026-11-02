"""Execution history is observable during work without replaying any adapter.

Uses labelled fixtures, Events and bounded waits; no model, network or DB calls.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.agent_framework.adapters.synthetic import make_agents, sample_inputs
from backend.agent_framework.api import create_app
from backend.agent_framework.contracts import Approval, FrameworkError, StepRecord
from backend.agent_framework.invocation import ROLE_CONTRACTS
from backend.agent_framework.ports import AdapterFailure
from backend.agent_framework.runtime import Workflow


ROLES = tuple(ROLE_CONTRACTS)
PREFIX = "/api/framework/v1"


def approval(context, decision="approve_criteria", **fields):
    return Approval(context=context, actor_id=context.actor_id, decision=decision, **fields)


def instrumented(*, fail_role=None, failure=None, transform=None, block_role=None,
                 entered=None, release=None):
    """One wrapper per port records actual calls, never inferred progress."""
    agents = make_agents()
    calls = []
    for role, (port, method_name, _input, _output) in ROLE_CONTRACTS.items():
        original = getattr(getattr(agents, port), method_name)

        def invoke(request, *, role=role, original=original):
            calls.append((request.context.run_id, role))
            if role == block_role:
                entered.set()
                if not release.wait(8):
                    raise RuntimeError("test adapter was not released")
            if role == fail_role:
                raise failure
            result = original(request)
            return transform(role, result) if transform else result

        agents = replace(agents, **{port: SimpleNamespace(**{method_name: invoke})})
    return agents, calls


def reviewed(workflow, inputs=None):
    context, document, asset = inputs or sample_inputs()
    start = workflow.start(context, document, asset)
    if start.execution_status == "failed":
        return context, start
    return context, workflow.approve(context.run_id, approval(context))


def assert_finished(record, status="completed"):
    assert record.status == status
    for timestamp in (record.recorded_at, record.started_at, record.finished_at):
        assert isinstance(timestamp, datetime)
        assert timestamp.utcoffset() == timedelta(0)
    assert isinstance(record.duration_ms, (int, float))
    assert math.isfinite(record.duration_ms) and record.duration_ms >= 0


def test_actual_role_sequence_is_separate_from_human_decisions_and_machine_verdict():
    agents, calls = instrumented()
    workflow = Workflow(agents, profile="test")
    context, document, asset = sample_inputs()
    started = workflow.start(context, document, asset)
    assert [(item.ordinal, item.role, item.status) for item in started.step_records] == [(1, "A02", "completed")]
    assert started.execution_status == "waiting_criteria"
    assert started.machine_verdict is None and started.human_decision == "unreviewed"
    result = workflow.approve(context.run_id, approval(context))
    assert [item.role for item in result.step_records] == list(ROLES)
    assert [item.ordinal for item in result.step_records] == list(range(1, 6))
    assert calls == [(context.run_id, role) for role in ROLES]
    for item in result.step_records:
        assert_finished(item)
        assert item.error is None and item.source_run_id is None and item.synthetic
    assert result.stages == ("A01", *ROLES)
    final = workflow.approve(context.run_id, approval(context, "approve_final"))
    assert final.step_records == result.step_records
    assert final.machine_verdict == "pass" and final.human_decision == "final_approved"
    timeline = workflow.get_steps(context.run_id)
    assert timeline.context == context and timeline.profile == "test" and timeline.storage == "memory"
    assert timeline.execution_status == "completed" and timeline.records == final.step_records


@pytest.mark.parametrize("failed_role", ROLES)
def test_failure_closes_only_attempted_stage_and_preserves_prior_completions(failed_role):
    agents, calls = instrumented(fail_role=failed_role,
        failure=AdapterFailure("FILE_HASH_MISMATCH", "The hash differs.", retryable=True))
    workflow = Workflow(agents)
    context, result = reviewed(workflow)
    expected_roles = ROLES[:ROLES.index(failed_role) + 1]
    assert [item.role for item in result.step_records] == list(expected_roles)
    assert calls == [(context.run_id, role) for role in expected_roles]
    for item in result.step_records[:-1]:
        assert_finished(item)
    failed = result.step_records[-1]
    assert_finished(failed, "failed")
    assert failed.error.code == "FILE_HASH_MISMATCH" and failed.error.retryable
    assert not failed.error.outcome_unknown and result.execution_status == "failed"
    before = workflow.get_steps(context.run_id)
    with pytest.raises(ValueError):
        workflow.approve(context.run_id, approval(context))
    assert workflow.get_steps(context.run_id) == before
    assert calls == [(context.run_id, role) for role in expected_roles]


def test_uncertain_completion_remains_unknown_without_retry_or_later_stages():
    agents, calls = instrumented(fail_role="A04_A05", failure=AdapterFailure(
        "REMOTE_TIMEOUT", "Completion is unknown.", outcome_unknown=True))
    workflow = Workflow(agents)
    context, result = reviewed(workflow)
    assert result.execution_status == "outcome_unknown"
    record = result.step_records[-1]
    assert_finished(record, "outcome_unknown")
    assert record.error.code == "REMOTE_TIMEOUT" and record.error.outcome_unknown
    assert calls == [(context.run_id, role) for role in ROLES[:3]]


@pytest.mark.parametrize("role", ROLES)
def test_scope_rejection_has_specific_step_error_without_claiming_completion(role):
    def tamper(current, result):
        return result.model_copy(update={"context": result.context.model_copy(
            update={"project_id": "forged-project"})}) if current == role else result

    agents, _calls = instrumented(transform=tamper)
    workflow = Workflow(agents)
    _context, result = reviewed(workflow)
    assert result.execution_status == "failed"
    # Keep the historical workflow category; stage detail identifies the rejected boundary.
    assert result.error.code == "contract_violation"
    assert result.step_records[-1].role == role
    assert result.step_records[-1].status == "failed"
    assert result.step_records[-1].error.code == "CONTEXT_MISMATCH"


def test_malformed_output_is_a_failed_attempt_and_not_completed_output():
    agents, _calls = instrumented(transform=lambda role, result:
        result.model_copy(update={"criteria": "not-criteria"}) if role == "A02" else result)
    result = Workflow(agents).start(*sample_inputs())
    assert result.criteria is None
    assert result.step_records[0].status == "failed"
    assert result.step_records[0].error.code == "CONTRACT_INVALID"


@pytest.mark.parametrize("failure", [RuntimeError("secret-provider-token"),
    AdapterFailure("invalid code secret-provider-token", "secret-provider-token")])
def test_step_errors_do_not_expose_arbitrary_provider_payloads(failure):
    agents, _calls = instrumented(fail_role="A02", failure=failure)
    result = Workflow(agents).start(*sample_inputs())
    record = result.step_records[0]
    assert record.status == "failed" and record.error.code == "ADAPTER_FAILED"
    assert "secret-provider-token" not in record.model_dump_json()


@pytest.mark.parametrize("role,status", [("A02", "extracting"), ("A03", "reviewing")])
def test_get_and_timeline_return_running_record_before_adapter_is_released(role, status):
    entered, release = Event(), Event()
    agents, _calls = instrumented(block_role=role, entered=entered, release=release)
    workflow = Workflow(agents)
    context, document, asset = sample_inputs()
    if role != "A02":
        workflow.start(context, document, asset)
    with ThreadPoolExecutor(max_workers=3) as pool:
        operation = pool.submit(workflow.start, context, document, asset) if role == "A02" else pool.submit(
            workflow.approve, context.run_id, approval(context))
        try:
            assert entered.wait(3), "adapter was not entered"
            snapshot = pool.submit(workflow.get, context.run_id).result(timeout=2)
            timeline = pool.submit(workflow.get_steps, context.run_id).result(timeout=2)
            assert snapshot.execution_status == timeline.execution_status == status
            running = snapshot.step_records[-1]
            assert running == timeline.records[-1] and running.role == role and running.status == "running"
            assert running.started_at is not None and running.finished_at is None
            assert running.duration_ms is None and running.error is None
            assert snapshot.criteria is None if role == "A02" else snapshot.evidence is None
        finally:
            release.set()
        assert operation.result(timeout=3).step_records[-1].status == "completed"


def test_api_live_timeline_and_snapshot_are_nonblocking_during_review():
    entered, release = Event(), Event()
    agents, _calls = instrumented(block_role="A03", entered=entered, release=release)
    app = create_app(agents, profile="test")
    context, document, asset = sample_inputs()
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=3) as pool:
        payload = {"context": context.model_dump(mode="json"), "document": document.model_dump(mode="json"),
                   "asset": asset.model_dump(mode="json")}
        assert client.post(PREFIX + "/runs", json=payload).status_code == 201
        run_path = PREFIX + "/runs/" + context.run_id
        operation = pool.submit(client.post, run_path + "/approvals", json=approval(context).model_dump(mode="json"))
        try:
            assert entered.wait(3)
            response = pool.submit(client.get, run_path + "/steps").result(timeout=2)
            snapshot = pool.submit(client.get, run_path).result(timeout=2)
            assert response.status_code == snapshot.status_code == 200
            timeline = response.json()
            assert timeline["storage"] == "memory" and timeline["profile"] == "test"
            assert timeline["execution_status"] == snapshot.json()["execution_status"] == "reviewing"
            assert timeline["records"][-1]["role"] == "A03" and timeline["records"][-1]["status"] == "running"
            assert response.headers["Cache-Control"] == "no-store"
        finally:
            release.set()
        assert operation.result(timeout=3).status_code == 200
        assert client.get(run_path + "/steps").json()["records"][-1]["status"] == "completed"
        assert client.get(PREFIX + "/runs/missing/steps").status_code == 404


def test_rereview_records_reuse_without_new_extraction_or_parent_mutation():
    agents, calls = instrumented()
    workflow = Workflow(agents)
    context, _result = reviewed(workflow)
    parent = workflow.approve(context.run_id, approval(context, "approve_changes", reason="Revise the poster."))
    parent_steps = workflow.get_steps(context.run_id)
    new_context, _document, asset = sample_inputs("child-run", "poster-v2", context.run_id)
    child = workflow.rereview(context.run_id, new_context, asset)
    assert len(child.step_records) == 1
    reuse = child.step_records[0]
    assert (reuse.ordinal, reuse.role, reuse.status, reuse.source_run_id) == (1, "A02", "reused", context.run_id)
    assert reuse.started_at is reuse.finished_at is reuse.duration_ms is reuse.error is None
    assert reuse.recorded_at.utcoffset() == timedelta(0)
    assert reuse.synthetic and child.stages == ("A08", "A01", "A02_draft_reused")
    before = tuple(calls)
    with pytest.raises(ValueError):
        workflow.approve(new_context.run_id, approval(new_context, "approve_final"))
    assert tuple(calls) == before
    result = workflow.approve(new_context.run_id, approval(new_context))
    assert result.step_records[0] == reuse
    assert [item.role for item in result.step_records] == list(ROLES)
    assert all(item.status == "completed" for item in result.step_records[1:])
    assert calls.count((context.run_id, "A02")) == 1 and (new_context.run_id, "A02") not in calls
    assert workflow.get(context.run_id) == parent and workflow.get_steps(context.run_id) == parent_steps


def test_duplicate_concurrent_approvals_do_not_duplicate_records_or_role_calls():
    agents, calls = instrumented()
    workflow = Workflow(agents)
    context, document, asset = sample_inputs()
    workflow.start(context, document, asset)
    barrier = Barrier(2)

    def approve_once():
        barrier.wait(timeout=3)
        try:
            return workflow.approve(context.run_id, approval(context)).execution_status
        except ValueError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve_once) for _ in range(2)]
        assert sorted(item.result(timeout=5) for item in futures) == ["conflict", "waiting_review"]
    timeline = workflow.get_steps(context.run_id)
    assert [item.role for item in timeline.records] == list(ROLES)
    assert calls == [(context.run_id, role) for role in ROLES]


def test_running_one_run_does_not_block_another_run_or_mix_records():
    entered, release = Event(), Event()
    base = make_agents()

    def extraction(request):
        if request.context.run_id == "blocked-run":
            entered.set()
            if not release.wait(8):
                raise RuntimeError("test adapter was not released")
        return base.extractor.extract(request)

    workflow = Workflow(replace(base, extractor=SimpleNamespace(extract=extraction)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        blocked = pool.submit(workflow.start, *sample_inputs("blocked-run"))
        try:
            assert entered.wait(3)
            other = pool.submit(workflow.start, *sample_inputs("other-run")).result(timeout=2)
            assert other.context.run_id == "other-run" and other.step_records[0].status == "completed"
            assert workflow.get_steps("blocked-run").records[0].status == "running"
        finally:
            release.set()
        assert blocked.result(timeout=3).step_records[0].status == "completed"


def test_returned_step_records_and_errors_are_isolated_from_internal_state():
    agents, _calls = instrumented(fail_role="A03", failure=AdapterFailure("LOOKUP_FAILED", "Lookup failed."))
    workflow = Workflow(agents)
    context, result = reviewed(workflow)
    timeline = workflow.get_steps(context.run_id)
    expected = timeline.model_copy(deep=True)
    object.__setattr__(result.step_records[-1].error, "code", "CORRUPTED")
    object.__setattr__(timeline.records[-1], "status", "completed")
    object.__setattr__(timeline.context, "project_id", "foreign")
    assert workflow.get_steps(context.run_id) == expected
    assert workflow.get(context.run_id).step_records[-1].error.code == "LOOKUP_FAILED"


def test_invalid_preflight_creates_no_record_and_memory_is_not_shared_by_instances():
    workflow = Workflow(make_agents())
    context, document, asset = sample_inputs()
    with pytest.raises(ValueError):
        workflow.start(context, document.model_copy(update={"document_version_id": "wrong"}), asset)
    with pytest.raises(KeyError):
        workflow.get_steps(context.run_id)
    workflow.start(context, document, asset)
    with pytest.raises(KeyError):
        Workflow(make_agents()).get_steps(context.run_id)


@pytest.mark.parametrize("change", [
    {"ordinal": 0}, {"role": "A08"}, {"status": "invented"},
    {"duration_ms": -1}, {"duration_ms": float("nan")}, {"duration_ms": float("inf")},
    {"started_at": None}, {"finished_at": None}, {"recorded_at": datetime(2026, 11, 2)},
    {"source_run_id": "unrelated-run"},
])
def test_step_contract_rejects_invalid_or_misleading_completed_records(change):
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    payload = dict(ordinal=1, role="A02", status="completed", recorded_at=now,
                   started_at=now, finished_at=now, duration_ms=0, synthetic=True)
    with pytest.raises(ValidationError):
        StepRecord.model_validate({**payload, **change})


def test_unknown_failure_contract_requires_explicit_outcome_uncertainty():
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        StepRecord(ordinal=1, role="A03", status="outcome_unknown", recorded_at=now,
                   started_at=now, finished_at=now, duration_ms=0,
                   error=FrameworkError(code="REMOTE_TIMEOUT", message="Timeout", outcome_unknown=False), synthetic=True)


@pytest.mark.parametrize("change", [
    {"finished_at": datetime(2026, 11, 2, tzinfo=timezone.utc)},
    {"duration_ms": 0},
    {"error": FrameworkError(code="FAILED", message="Failure")},
    {"started_at": None},
])
def test_running_contract_cannot_claim_a_terminal_result(change):
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    payload = dict(ordinal=1, role="A02", status="running", recorded_at=now,
                   started_at=now, synthetic=True)
    with pytest.raises(ValidationError):
        StepRecord.model_validate({**payload, **change})


@pytest.mark.parametrize("change", [
    {"source_run_id": None}, {"role": "A03"}, {"duration_ms": 0},
    {"started_at": datetime(2026, 11, 2, tzinfo=timezone.utc)},
    {"finished_at": datetime(2026, 11, 2, tzinfo=timezone.utc)},
    {"error": FrameworkError(code="FAILED", message="Failure")},
])
def test_reused_record_cannot_claim_a_new_model_invocation(change):
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    payload = dict(ordinal=1, role="A02", status="reused", recorded_at=now,
                   source_run_id="parent-run", synthetic=True)
    with pytest.raises(ValidationError):
        StepRecord.model_validate({**payload, **change})


def test_backwards_wall_clock_does_not_make_elapsed_time_negative(monkeypatch):
    import backend.agent_framework.runtime as runtime

    started = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    wall_times = iter((started, started - timedelta(seconds=30)))
    monotonic_times = iter((9_000_000_000, 9_250_000_000))

    class BackwardsWallClock:
        @staticmethod
        def now(tz):
            assert tz == timezone.utc
            return next(wall_times)

    monkeypatch.setattr(runtime, "datetime", BackwardsWallClock)
    monkeypatch.setattr(runtime, "perf_counter_ns", lambda: next(monotonic_times))
    result = Workflow(make_agents()).start(*sample_inputs())
    assert result.execution_status == "waiting_criteria"
    record = result.step_records[0]
    assert record.status == "completed"
    assert record.recorded_at == record.started_at == record.finished_at == started
    assert record.duration_ms == 250.0
