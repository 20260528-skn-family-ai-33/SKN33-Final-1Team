from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest

from backend.agent_framework.contracts import (
    Context, SourceRef, Criterion, AssetRef, Evidence, EvidenceBundle, ImageEvidence,
    Observation, InspectionResult, ValidateRequest, ValidatedItem, ReportDraft,
    ReportRequest,
)
from backend.agent_framework.invocation import InvocationFailure, invoke_role
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
    bundle = EvidenceBundle(context=context, context_id="q1", status="complete", evidence=(evidence,), synthetic=True)
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


@pytest.mark.parametrize('observed',['3000','2400'])
def test_typed_metadata_is_review_until_the_inspection_contract_supports_the_attribute(observed):
    from backend.agent_framework.contracts import RequirementDetails
    req=request();quote='가로 3000픽셀';source=SourceRef(document_version_id='d1',page=1,block_id='b1',quote=quote,source_hash=sha256(quote.encode()).hexdigest())
    value=source.model_copy(update={'quote':'3000','source_hash':sha256(b'3000').hexdigest()})
    criterion=req.criteria[0].model_copy(update={'text':quote,'source':source,'expected':'3000',
        'details':RequirementDetails(target='file_metadata',field='pixel_width',expected_source=value)})
    bundle=req.evidence.model_copy(update={'evidence':(req.evidence.evidence[0].model_copy(update={'source':source}),)})
    inspection=req.inspection.model_copy(update={'observations':(req.inspection.observations[0].model_copy(update={'observed':observed}),)})
    req=req.model_copy(update={'criteria':(criterion,),'evidence':bundle,'inspection':inspection})
    result=ReferenceValidator().validate(req)
    assert result.verdict=='review' and 'typed_attribute_review_required' in result.items[0].issues
    forged=result.model_copy(update={'verdict':'pass','items':(result.items[0].model_copy(update={'verdict':'pass','issues':()}),)})
    with pytest.raises(ValueError):validate_validated(req,forged)


@pytest.mark.parametrize('observed',['밤의 도서관','밤의도서관'])
def test_unimplemented_allowance_interpretation_cannot_become_a_literal_pass_or_fail(observed):
    from backend.agent_framework.contracts import RequirementDetails
    req=request();quote='제목 밤의 도서관. 허용 범위: 공백 차이를 허용한다.'
    source=SourceRef(document_version_id='d1',page=1,block_id='b1',quote=quote,source_hash=sha256(quote.encode()).hexdigest())
    value=source.model_copy(update={'quote':'밤의 도서관','source_hash':sha256('밤의 도서관'.encode()).hexdigest()})
    qualifier=source.model_copy(update={'quote':'허용 범위: 공백 차이를 허용한다.','source_hash':sha256('허용 범위: 공백 차이를 허용한다.'.encode()).hexdigest()})
    criterion=req.criteria[0].model_copy(update={'text':quote,'kind':'text','operator':'contains','expected':'밤의 도서관','source':source,
        'details':RequirementDetails(target='poster_text',field='title_phrase',expected_source=value,qualifiers=(qualifier,))})
    bundle=req.evidence.model_copy(update={'evidence':(req.evidence.evidence[0].model_copy(update={'source':source}),)})
    inspection=req.inspection.model_copy(update={'observations':(req.inspection.observations[0].model_copy(update={'observed':observed}),)})
    result=ReferenceValidator().validate(req.model_copy(update={'criteria':(criterion,),'evidence':bundle,'inspection':inspection}))
    assert result.verdict=='review' and 'qualifier_interpretation_required' in result.items[0].issues


@pytest.mark.parametrize('normalized', [None, '2026-10-25'])
def test_partial_date_cannot_pass_a_different_year_by_substring_match(normalized):
    from backend.agent_framework.contracts import RequirementDetails
    req = request()
    quote = '기간: 2026.10.23 ~ 10.25'
    source = SourceRef(document_version_id='d1', page=1, block_id='b1', quote=quote,
                       source_hash=sha256(quote.encode()).hexdigest())
    value = source.model_copy(update={'quote': '10.25', 'source_hash': sha256(b'10.25').hexdigest()})
    criterion = req.criteria[0].model_copy(update={
        'text': quote, 'kind': 'text', 'operator': 'contains', 'expected': '10.25', 'source': source,
        'details': RequirementDetails(target='poster_text', field='end_date',
                                      expected_source=value, normalized_expected=normalized),
    })
    bundle = req.evidence.model_copy(update={'evidence': (
        req.evidence.evidence[0].model_copy(update={'source': source}),
    )})
    inspection = req.inspection.model_copy(update={'observations': (
        req.inspection.observations[0].model_copy(update={'observed': '2027.10.25', 'verdict': 'pass'}),
    )})
    req = req.model_copy(update={'criteria': (criterion,), 'evidence': bundle, 'inspection': inspection})
    result = ReferenceValidator().validate(req)
    assert result.verdict == 'review'
    assert 'date_comparison_review_required' in result.items[0].issues
    forged = result.model_copy(update={'verdict': 'pass', 'items': (
        result.items[0].model_copy(update={'verdict': 'pass', 'issues': ()}),
    )})
    with pytest.raises(ValueError):
        validate_validated(req, forged)


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
        validate_evidence(req.context, req.criteria, bundle)


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


def text_request(expected, operator, model_verdict="pass", observed="unrelated phrase"):
    req = request()
    data = req.model_dump(mode="json")
    data["criteria"][0].update(kind="text", expected=expected, operator=operator)
    data["inspection"]["observations"][0].update(observed=observed, verdict=model_verdict)
    return ValidateRequest.model_validate(data)


@pytest.mark.parametrize("expected", [
    pytest.param("", id="empty"),
    pytest.param(" ", id="space"),
    pytest.param("\t\r\n", id="ascii-whitespace"),
    pytest.param("\u00a0\u3000", id="unicode-whitespace"),
])
@pytest.mark.parametrize("operator", ["equals", "contains"])
@pytest.mark.parametrize("model_verdict", ["pass", "fail", "review"])
def test_blank_text_expectation_requires_review(expected, operator, model_verdict):
    result = ReferenceValidator().validate(text_request(expected, operator, model_verdict))
    assert result.verdict == result.items[0].verdict == "review"
    assert result.items[0].issues == ("missing_expected_value",)


@pytest.mark.parametrize("operator", ["equals", "contains"])
def test_custom_validator_cannot_pass_a_blank_text_expectation(operator):
    req = text_request(" ", operator)
    result = ReferenceValidator().validate(req)
    forged = result.model_copy(update={"verdict": "pass", "items": (
        ValidatedItem(criterion_id="count", verdict="pass"),)})
    with pytest.raises(ValueError, match="VALIDATOR_UNSUPPORTED_PASS"):
        validate_validated(req, forged)
    adapter = SimpleNamespace(validate=lambda _request: forged.model_dump(mode="json"))
    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A06", adapter, req)
    assert failure.value.code == "VALIDATOR_UNSUPPORTED_PASS"


@pytest.mark.parametrize("operator", ["equals", "contains"])
def test_missing_observation_does_not_hide_missing_expectation(operator):
    req = text_request("", operator)
    req = changed(req, ["inspection", "observations"], [])
    result = ReferenceValidator().validate(req)
    assert result.verdict == "review"
    assert set(result.items[0].issues) == {"missing_observation", "missing_expected_value"}


@pytest.mark.parametrize("operator,expected,observed,verdict", [
    pytest.param("equals", "Hello", "Hello", "pass", id="equals-match"),
    pytest.param("contains", "Hello", "Say Hello today", "pass", id="contains-match"),
    pytest.param("equals", " Hello ", "Hello", "fail", id="equals-preserves-spaces"),
    pytest.param("contains", " Hello ", "Hello", "fail", id="contains-preserves-spaces"),
    pytest.param("equals", "0", "0", "pass", id="zero-text"),
])
def test_nonblank_text_expectations_preserve_comparison_semantics(operator, expected, observed, verdict):
    result = ReferenceValidator().validate(text_request(expected, operator, observed=observed))
    assert result.verdict == verdict
    assert "missing_expected_value" not in result.items[0].issues


def test_visual_presence_does_not_require_a_text_expectation():
    req = changed(request(), ["criteria", 0, "kind"], "visual")
    req = changed(req, ["criteria", 0, "operator"], "present")
    req = changed(req, ["criteria", 0, "expected"], "")
    assert ReferenceValidator().validate(req).verdict == "pass"


@pytest.mark.parametrize("operator", ["equals", "contains"])
def test_custom_validator_cannot_fail_an_unresolved_text_expectation(operator):
    req = text_request("", operator)
    result = ReferenceValidator().validate(req)
    forged = result.model_copy(update={"verdict": "fail", "items": (
        ValidatedItem(criterion_id="count", verdict="fail", issues=("missing_expected_value",)),)})
    with pytest.raises(ValueError, match="VALIDATOR_UNRESOLVED_EXPECTATION"):
        validate_validated(req, forged)
