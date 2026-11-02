from copy import deepcopy
from hashlib import sha256

import pytest

from backend.agent_framework.contracts import (
    Context, SourceRef, Criterion, AssetRef, Evidence, EvidenceBundle, ImageEvidence,
    Observation, InspectionResult, ValidateRequest, ValidatedItem, ReportDraft,
    ReportRequest, RetrieveRequest,
)
from backend.agent_framework.validation import ReferenceValidator, validate_evidence, validate_report, validate_validated


def request():
    context = Context(project_id="p1", run_id="r1", actor_id="u1", document_version_id="d1",
                      criteria_version_id="c1", asset_version_id="a1")
    source = SourceRef(document_version_id="d1", page=1, block_id="b1", quote="three cups",
                       source_hash=sha256(b"three cups").hexdigest())
    criterion = Criterion(criterion_id="count", text="Count cups", kind="number", expected="3",
                          operator="equals", source=source)
    asset = AssetRef(file_id="file1", asset_version_id="a1", sha256="a" * 64, width=100, height=100)
    evidence = Evidence(evidence_id="e1", project_id="p1", kind="current_requirement", criterion_ids=("count",),
                        source_record_id="b1", source=source)
    bundle = EvidenceBundle(context=context, retrieval_id="q1", status="complete", evidence=(evidence,), synthetic=True)
    observation = Observation(criterion_id="count", observed="3", verdict="pass",
                              image=ImageEvidence(asset_version_id="a1", asset_hash="a" * 64), evidence_ids=("e1",))
    return ValidateRequest(context=context, criteria=(criterion,), asset=asset, evidence=bundle,
                           inspection=InspectionResult(context=context, observations=(observation,), synthetic=True))


def changed(req, path, value):
    data = req.model_dump(mode="json")
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    return ValidateRequest.model_validate(data)


def test_wrong_count_cannot_be_passed_by_model():
    req = changed(request(), ["inspection", "observations", 0, "observed"], "2")
    result = ReferenceValidator().validate(req)
    assert result.verdict == "fail"
    assert result.items[0].issues == ("value_mismatch",)


@pytest.mark.parametrize("path,value", [
    (["inspection", "observations"], []),
    (["inspection", "observations", 0, "uncertainty"], "occluded"),
    (["inspection", "observations", 0, "evidence_ids"], []),
    (["inspection", "observations", 0, "observed"], "NaN"),
    (["evidence", "status"], "stale"),
    (["evidence", "status"], "unavailable"),
])
def test_incomplete_input_never_passes(path, value):
    assert ReferenceValidator().validate(changed(request(), path, value)).verdict == "review"


@pytest.mark.parametrize("path,value", [
    (["evidence", "context", "project_id"], "foreign"),
    (["evidence", "evidence", 0, "project_id"], "foreign"),
    (["evidence", "evidence", 0, "source", "document_version_id"], "old"),
    (["inspection", "observations", 0, "image", "asset_version_id"], "old"),
    (["inspection", "observations", 0, "image", "asset_hash"], "b" * 64),
    (["inspection", "observations", 0, "evidence_ids"], ["invented"]),
    (["inspection", "observations", 0, "criterion_id"], "invented"),
    (["evidence", "evidence", 0, "kind"], "historical_context"),
])
def test_foreign_or_fabricated_evidence_is_rejected(path, value):
    with pytest.raises(ValueError):
        ReferenceValidator().validate(changed(request(), path, value))


def test_duplicate_observation_is_not_double_counted():
    req = request()
    with pytest.raises(ValueError, match="DUPLICATE_OBSERVATION"):
        ReferenceValidator().validate(changed(req, ["inspection", "observations"],
                                            [o.model_dump() for o in req.inspection.observations] * 2))


def test_a06_cannot_hide_missing_observation():
    req = changed(request(), ["inspection", "observations"], [])
    result = ReferenceValidator().validate(req)
    forged = result.model_copy(update={"verdict": "pass", "items": (ValidatedItem(criterion_id="count", verdict="pass"),)})
    with pytest.raises(ValueError, match="UNSUPPORTED_PASS"):
        validate_validated(req, forged)


def test_report_cannot_rewrite_judgment_or_invent_citations():
    req = changed(request(), ["inspection", "observations", 0, "observed"], "2")
    result = ReferenceValidator().validate(req)
    inp = ReportRequest(context=req.context, validated=result, evidence=req.evidence)
    with pytest.raises(ValueError, match="CHANGED_VERDICT"):
        validate_report(inp, ReportDraft(context=req.context, verdict="pass", summary="OK", evidence_ids=("e1",)))
    with pytest.raises(ValueError, match="UNKNOWN_REPORT_CITATION"):
        validate_report(inp, ReportDraft(context=req.context, verdict="fail", summary="Bad", evidence_ids=("fake",)))


def test_failed_required_condition_survives_other_missing_inputs():
    req = changed(request(), ["inspection", "observations", 0, "observed"], "2")
    second = req.criteria[0].model_copy(update={"criterion_id": "other"})
    req = req.model_copy(update={"criteria": (*req.criteria, second)})
    assert ReferenceValidator().validate(req).verdict == "fail"


def test_stale_graph_cannot_claim_complete():
    req = request()
    bundle = req.evidence.model_copy(update={"postgres_revision": 2, "graph_revision": 1})
    with pytest.raises(ValueError, match="STALE_GRAPH"):
        validate_evidence(RetrieveRequest(context=req.context, criteria=req.criteria), bundle)


def test_uncertain_value_is_not_a_confirmed_mismatch():
    req = changed(request(), ["inspection", "observations", 0, "observed"], "2")
    req = changed(req, ["inspection", "observations", 0, "uncertainty"], "count obscured")
    result = ReferenceValidator().validate(req)
    assert result.verdict == "review"
    assert "value_mismatch" not in result.items[0].issues


def test_model_failure_with_matching_value_is_an_unresolved_conflict():
    req = changed(request(), ["inspection", "observations", 0, "verdict"], "fail")
    result = ReferenceValidator().validate(req)
    assert result.verdict == "review"
    assert "verdict_conflict" in result.items[0].issues


def test_unsupported_comparison_does_not_pass():
    req = changed(request(), ["criteria", 0, "kind"], "text")
    req = changed(req, ["criteria", 0, "operator"], "gte")
    result = ReferenceValidator().validate(req)
    assert result.verdict == "review"
    assert "unsupported_criterion" in result.items[0].issues


def test_blank_observation_is_missing_data_not_confirmed_failure():
    req = changed(request(), ["criteria", 0, "kind"], "text")
    req = changed(req, ["inspection", "observations", 0, "observed"], "")
    result = ReferenceValidator().validate(req)
    assert result.verdict == "review"
    assert "missing_observation" in result.items[0].issues
