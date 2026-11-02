"""A07 checks available provenance before work without inventing missing criteria."""
from types import SimpleNamespace

import pytest

from backend.agent_framework.adapters.generate_examples import role_examples
from backend.agent_framework.adapters.synthetic import make_agents
from backend.agent_framework.contracts import ReportDraft
from backend.agent_framework.invocation import InvocationFailure, invoke_role
from backend.agent_framework.validation import validate_evidence, validate_report


def replace_evidence(request, first):
    bundle = request.evidence.model_copy(update={"evidence": (first,) + request.evidence.evidence[1:]})
    return request.model_copy(update={"evidence": bundle})


def evidence_kind(request, kind):
    first = request.evidence.evidence[0]
    updates = {"kind": kind}
    if kind == "approved_exception":
        updates.update(approval_id="exception-approval", applicable_asset_version_id=request.context.asset_version_id)
    if kind == "historical_context":
        updates["source"] = first.source.model_copy(update={"document_version_id": "past-document"})
    return first.model_copy(update=updates)


@pytest.mark.parametrize("kind", ("current_requirement", "historical_context", "approved_exception"))
@pytest.mark.parametrize("fault,expected", (("project", "EVIDENCE_PROJECT_MISMATCH"),
                                            ("hash", "EVIDENCE_HASH_MISMATCH")))
def test_reporter_never_receives_foreign_project_or_forged_source_hash(kind, fault, expected):
    request, response = role_examples()["A07"]
    evidence = evidence_kind(request, kind)
    if fault == "project":
        evidence = evidence.model_copy(update={"project_id": "another-project"})
    else:
        evidence = evidence.model_copy(update={"source": evidence.source.model_copy(update={"source_hash": "0" * 64})})
    request = replace_evidence(request, evidence)
    calls = []
    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A07", SimpleNamespace(report=lambda value: calls.append(value) or response), request)
    assert failure.value.code == expected
    assert calls == []


@pytest.mark.parametrize("kind,field,expected", (
    ("current_requirement", "document", "EVIDENCE_SOURCE_MISMATCH"),
    ("approved_exception", "document", "EXCEPTION_SCOPE_MISMATCH"),
    ("approved_exception", "asset", "EXCEPTION_SCOPE_MISMATCH"),
))
def test_current_document_and_exception_asset_scope_are_checked_before_reporting(kind, field, expected):
    request, response = role_examples()["A07"]
    evidence = evidence_kind(request, kind)
    if field == "document":
        evidence = evidence.model_copy(update={"source": evidence.source.model_copy(update={"document_version_id": "wrong-document"})})
    else:
        evidence = evidence.model_copy(update={"applicable_asset_version_id": "wrong-asset"})
    calls = []
    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A07", SimpleNamespace(report=lambda value: calls.append(value) or response),
                    replace_evidence(request, evidence))
    assert failure.value.code == expected
    assert not calls


@pytest.mark.parametrize("fault,expected", (
    ("duplicate_evidence", "DUPLICATE_EVIDENCE"),
    ("duplicate_validated", "DUPLICATE_VALIDATED_ITEM"),
    ("unknown_criterion", "UNKNOWN_EVIDENCE_CRITERION"),
    ("empty_has_evidence", "EMPTY_RETRIEVAL_HAS_EVIDENCE"),
    ("stale_marked_complete", "STALE_GRAPH_MARKED_COMPLETE"),
))
def test_report_input_envelope_invariants_stop_provider_calls(fault, expected):
    request, response = role_examples()["A07"]
    if fault == "duplicate_evidence":
        request = request.model_copy(update={"evidence": request.evidence.model_copy(
            update={"evidence": request.evidence.evidence + (request.evidence.evidence[0],)})})
    elif fault == "duplicate_validated":
        request = request.model_copy(update={"validated": request.validated.model_copy(
            update={"items": request.validated.items + (request.validated.items[0],)})})
    elif fault == "unknown_criterion":
        request = replace_evidence(request, request.evidence.evidence[0].model_copy(update={"criterion_ids": ("unknown",)}))
    elif fault == "empty_has_evidence":
        request = request.model_copy(update={"evidence": request.evidence.model_copy(update={"status": "empty"})})
    else:
        request = request.model_copy(update={"evidence": request.evidence.model_copy(
            update={"status": "complete", "postgres_revision": 3, "graph_revision": 2})})
    calls = []
    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A07", SimpleNamespace(report=lambda value: calls.append(value) or response), request)
    assert failure.value.code == expected
    assert not calls


def test_valid_history_and_scoped_exception_can_be_cited_without_changing_failed_verdict():
    examples = role_examples("fail")
    request, _ = examples["A07"]
    historical = evidence_kind(request, "historical_context").model_copy(update={"evidence_id": "history-1"})
    exception = evidence_kind(request, "approved_exception").model_copy(update={"evidence_id": "exception-1"})
    request = request.model_copy(update={"evidence": request.evidence.model_copy(
        update={"evidence": request.evidence.evidence + (historical, exception)})})
    # The shared retrieval boundary permits this same provenance. History may
    # refer to an older document; it is never converted to current observation.
    validate_evidence(examples["A03"][0], request.evidence)
    response = ReportDraft(context=request.context, verdict="fail", summary="Failure with historical context.",
                           evidence_ids=tuple(e.evidence_id for e in request.evidence.evidence), synthetic=True)
    result = invoke_role("A07", SimpleNamespace(report=lambda _request: response), request)
    assert result.verdict == request.validated.verdict == "fail"
    assert "history-1" in result.evidence_ids and "exception-1" in result.evidence_ids


@pytest.mark.parametrize("scenario", ("pass", "fail", "review", "stale", "unavailable"))
def test_existing_scenario_reports_are_preserved(scenario):
    request, expected = role_examples(scenario)["A07"]
    result = invoke_role("A07", make_agents(scenario).reporter, request)
    assert result == expected


def test_empty_retrieval_can_still_report_the_supplied_review_verdict():
    request, response = role_examples("unavailable")["A07"]
    request = request.model_copy(update={"evidence": request.evidence.model_copy(update={"status": "empty"})})
    result = invoke_role("A07", SimpleNamespace(report=lambda _request: response), request)
    assert result.verdict == "review" and result.evidence_ids == ()


def test_direct_report_validation_also_rejects_invalid_evidence_and_preserves_citation_rules():
    request, response = role_examples()["A07"]
    foreign = request.evidence.evidence[0].model_copy(update={"project_id": "another-project"})
    with pytest.raises(ValueError, match="EVIDENCE_PROJECT_MISMATCH"):
        validate_report(replace_evidence(request, foreign), response)
    for ids, error in (((), "REPORT_CITATION_REQUIRED"),
                       (("invented",), "UNKNOWN_REPORT_CITATION"),
                       ((response.evidence_ids[0],) * 2, "DUPLICATE_REPORT_CITATION")):
        with pytest.raises(ValueError, match=error):
            validate_report(request, response.model_copy(update={"evidence_ids": ids}))
