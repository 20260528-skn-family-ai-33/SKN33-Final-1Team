"""A06 policy examples with hand-set outcomes, not model accuracy measurements."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.agent_framework.adapters.generate_examples import role_examples
from backend.agent_framework.composition import TeamConfiguration
from backend.agent_framework.contracts import ValidateRequest, RunSnapshot
from backend.agent_framework.invocation import InvocationFailure, invoke_role
from backend.agent_framework.validation import ReferenceValidator
from tests.agent_framework.test_validation import request, changed, text_request


def validate(req):
    return invoke_role("A06", ReferenceValidator(), req)


def number_request(expected, expected_unit, observed, observed_unit, operator="equals"):
    data = request().model_dump(mode="json")
    data["criteria"][0].update(expected=expected, unit=expected_unit, operator=operator)
    data["inspection"]["observations"][0].update(observed=observed, observed_unit=observed_unit)
    return ValidateRequest.model_validate(data)


@pytest.mark.parametrize("expected,unit,observed,observed_unit,operator,verdict", [
    ("10,000원", "원", "10000 원", None, "equals", "pass"),
    ("10000", "원", "10,000", "원", "equals", "pass"),
    ("10000", "원", "9,999원", None, "equals", "fail"),
    ("3", "개", "3", None, "equals", "review"),
    ("3", "개", "3kg", None, "equals", "review"),
    ("3", "개", "3kg", "개", "equals", "review"),
    ("1", "kg", "1000", "g", "equals", "review"),
    ("3", "개", "30개", None, "equals", "fail"),
    ("3", "개", "3개", None, "equals", "pass"),
    ("3", None, "4", None, "gte", "pass"),
    ("3", None, "4", None, "lte", "fail"),
    ("0.30", None, "0.3", None, "equals", "pass"),
    ("0.001", None, "0.001", None, "equals", "pass"),
    ("1000000", None, "1,000,000", None, "equals", "pass"),
    ("-1000", None, "-1,000", None, "equals", "pass"),
    ("0", None, "0", None, "equals", "pass"),
    ("-1.5", None, "-1.50", None, "equals", "pass"),
    ("1000", None, "1,00", None, "equals", "review"),
    ("1000", None, "1e3", None, "equals", "review"),
    ("3", None, "3 or 4", None, "equals", "review"),
    ("3", None, "three", None, "equals", "review"),
    ("3", None, "NaN", None, "equals", "review"),
    ("3", None, "Infinity", None, "equals", "review"),
    ("3", None, "3", " ", "equals", "review"),
    ("invalid", None, "3", None, "equals", "review"),
])
def test_numeric_policy(expected, unit, observed, observed_unit, operator, verdict):
    result = validate(number_request(expected, unit, observed, observed_unit, operator))
    assert result.verdict == result.items[0].verdict == verdict
    if verdict == "review":
        assert "value_mismatch" not in result.items[0].issues


@pytest.mark.parametrize("raw", ["0,001", "0,500", "00,500", "001,000", "+0,500", "-0,001"])
@pytest.mark.parametrize("side", ["expected", "observed"])
def test_ambiguous_leading_zero_comma_numbers_require_review(raw, side):
    req = number_request(raw if side == "expected" else "1", None,
                         raw if side == "observed" else "1", None)
    result = validate(req)
    item = result.items[0]
    assert result.verdict == item.verdict == "review"
    assert item.issues == (("numeric_expectation_invalid" if side == "expected" else "numeric_observation_invalid"),)
    assert item.comparison_expected is None and item.comparison_observed is None


@pytest.mark.parametrize("kind", ["text", "number"])
@pytest.mark.parametrize("fault", ["citation", "requirement", "stale", "unavailable", "uncertainty"])
def test_evidence_gap_prevents_confirming_a_mismatch(kind, fault):
    req = text_request("Hello", "equals", observed="Different") if kind == "text" else number_request("3", "개", "2", "개")
    if fault in {"stale", "unavailable"}:
        req = changed(req, ["evidence", "status"], fault)
    elif fault == "uncertainty":
        req = changed(req, ["inspection", "observations", 0, "uncertainty"], "unclear")
    else:
        req = changed(req, ["inspection", "observations", 0, "evidence_ids"], [])
        if fault == "requirement":
            req = changed(req, ["evidence", "evidence"], [])
    result = validate(req)
    assert result.verdict == "review"
    assert "value_mismatch" not in result.items[0].issues
    assert result.items[0].comparison_observed is None


@pytest.mark.parametrize("status", ["unknown", "unreadable", "not_checked"])
def test_non_observed_states_cannot_pass_even_with_matching_values(status):
    req = changed(request(), ["inspection", "observations", 0, "observation_status"], status)
    assert validate(req).verdict == "review"


def test_missing_status_is_unknown_not_an_inferred_observation():
    data = request().model_dump(mode="json")
    del data["inspection"]["observations"][0]["observation_status"]
    result = invoke_role("A06", ReferenceValidator(), data)
    assert result.verdict == "review"
    assert result.items[0].observation_status == "unknown"


@pytest.mark.parametrize("kind,operator,expected,model_verdict,verdict", [
    ("text", "contains", "Hello", "fail", "fail"),
    ("text", "equals", "Hello", "pass", "review"),
    ("visual", "present", "", "fail", "fail"),
    ("visual", "present", "", "pass", "review"),
    ("number", "equals", "0", "fail", "review"),
])
def test_explicit_absence_is_not_unreadability_or_numeric_zero(kind, operator, expected, model_verdict, verdict):
    data = request().model_dump(mode="json")
    data["criteria"][0].update(kind=kind, operator=operator, expected=expected)
    data["inspection"]["observations"][0].update(observation_status="absent", observed="", verdict=model_verdict)
    result = validate(ValidateRequest.model_validate(data))
    assert result.verdict == verdict
    assert result.items[0].observed == ""
    assert result.items[0].comparison_observed is None


@pytest.mark.parametrize("kind,operator,expected,observed,verdict", [
    ("text", "equals", "Hello", "Hello", "review"),
    ("text", "contains", "Hello", "Hello", "review"),
    ("text", "contains", "Hello", "Hello festival", "review"),
    ("visual", "present", "LOGO", "LOGO", "review"),
    ("text", "equals", "Hello", "Different", "fail"),
    ("text", "contains", "Hello", "Different", "fail"),
])
def test_absence_with_matching_observed_text_is_an_unresolved_conflict(kind, operator, expected, observed, verdict):
    req = text_request(expected, operator, model_verdict="fail", observed=observed)
    req = changed(req, ["criteria", 0, "kind"], kind)
    req = changed(req, ["inspection", "observations", 0, "observation_status"], "absent")
    result = validate(req)
    assert result.verdict == result.items[0].verdict == verdict
    assert result.items[0].issues == (("verdict_conflict" if verdict == "review" else "observed_absence"),)
    assert result.items[0].observed == observed
    assert result.items[0].comparison_observed is None


@pytest.mark.parametrize("kind,operator", [("visual", "manual"), ("visual", "equals"), ("unsupported", "present")])
def test_unsupported_visual_measurements_require_review(kind, operator):
    req = changed(request(), ["criteria", 0, "kind"], kind)
    req = changed(req, ["criteria", 0, "operator"], operator)
    result = validate(req)
    assert result.verdict == "review"
    assert "unsupported_criterion" in result.items[0].issues


@pytest.mark.parametrize("required,uncertain,verdict", [(False, False, "pass"), (False, True, "pass"),
                                                       (True, False, "fail"), (True, True, "review")])
def test_optional_results_remain_visible_without_changing_the_required_aggregate(required, uncertain, verdict):
    req = request()
    extra = req.criteria[0].model_copy(update={"criterion_id": "extra", "required": required})
    evidence = req.evidence.evidence[0].model_copy(update={"criterion_ids": ("count", "extra")})
    observation = req.inspection.observations[0].model_copy(update={
        "criterion_id": "extra", "observed": "2", "uncertainty": "unclear" if uncertain else None})
    req = req.model_copy(update={"criteria": (*req.criteria, extra),
        "evidence": req.evidence.model_copy(update={"evidence": (evidence,)}),
        "inspection": req.inspection.model_copy(update={"observations": (*req.inspection.observations, observation)})})
    result = validate(req)
    assert result.verdict == verdict
    assert result.items[1].verdict == ("review" if uncertain else "fail")
    assert result.items[1].required is required


def test_no_required_conditions_cannot_produce_an_overall_pass():
    result = validate(changed(request(), ["criteria", 0, "required"], False))
    assert result.verdict == "review" and result.items[0].verdict == "pass"


def test_comparison_values_and_original_evidence_reach_a07_unchanged():
    req = number_request("10000", "원", "10,000 원", None)
    result = validate(req)
    item = result.items[0]
    assert (item.expected, item.expected_unit, item.observed, item.observed_unit) == ("10000", "원", "10,000 원", None)
    assert (item.comparison_expected, item.comparison_observed, item.comparison_unit) == ("10000", "10000", "원")
    assert item.image == req.inspection.observations[0].image and item.image.bbox is None
    assert item.evidence_ids == ("e1",)
    from backend.agent_framework.contracts import ReportRequest, ReportDraft
    captured = []
    report = ReportRequest(context=req.context, validated=result, evidence=req.evidence)
    response = ReportDraft(context=req.context, verdict="pass", summary="Validated input received.", evidence_ids=("e1",))
    invoke_role("A07", SimpleNamespace(report=lambda value: captured.append(value) or response), report)
    assert captured[0].validated == result


@pytest.mark.parametrize("field,value", [("expected", "forged"), ("observed", "forged"),
    ("required", False), ("comparison_observed", "99"), ("comparison_unit", "kg"),
    ("evidence_ids", ()), ("image", None), ("observation_status", "absent")])
def test_custom_a06_cannot_forge_comparison_details(field, value):
    req = request()
    result = validate(req)
    item = result.items[0].model_copy(update={field: value})
    forged = result.model_copy(update={"items": (item,)})
    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A06", SimpleNamespace(validate=lambda _request: forged), req)
    assert failure.value.code == "VALIDATOR_CHANGED_EVIDENCE"


@pytest.mark.parametrize("fault", ["citation", "status", "unit"])
@pytest.mark.parametrize("forced", ["pass", "fail"])
def test_custom_a06_cannot_resolve_missing_information_by_changing_the_verdict(fault, forced):
    req = number_request("3", "개", "2", "개")
    path, value = {"citation": (["inspection", "observations", 0, "evidence_ids"], []),
                   "status": (["inspection", "observations", 0, "observation_status"], "unknown"),
                   "unit": (["inspection", "observations", 0, "observed_unit"], None)}[fault]
    req = changed(req, path, value)
    result = validate(req)
    forged = result.model_copy(update={"verdict": forced, "items": (result.items[0].model_copy(update={"verdict": forced}),)})
    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A06", SimpleNamespace(validate=lambda _request: forged), req)
    assert failure.value.code == ("VALIDATOR_UNSUPPORTED_PASS" if forced == "pass" else "VALIDATOR_UNRESOLVED_OBSERVATION")


@pytest.mark.parametrize("source,forced,issues,code", [
    ("pass", "fail", None, "VALIDATOR_UNSUPPORTED_FAILURE"),
    ("fail", "review", None, "VALIDATOR_HID_CONFIRMED_FAILURE"),
    ("review", "review", (), "VALIDATOR_DROPPED_LIMITATIONS"),
    ("pass", "pass", ("uncertain_observation",), "VALIDATOR_UNSUPPORTED_PASS"),
])
def test_custom_a06_rejects_unjustified_verdicts_and_lost_limitations(source, forced, issues, code):
    req = request()
    if source == "fail":
        req = changed(req, ["inspection", "observations", 0, "observed"], "2")
    elif source == "review":
        req = changed(req, ["inspection", "observations", 0, "uncertainty"], "unclear")
    result = validate(req)
    assert result.verdict == source
    item = result.items[0].model_copy(update={"verdict": forced,
        "issues": result.items[0].issues if issues is None else issues})
    forged = result.model_copy(update={"verdict": forced, "items": (item,)})
    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A06", SimpleNamespace(validate=lambda _request: forged), req)
    assert failure.value.code == code


@pytest.mark.parametrize("contract", [ValidateRequest, TeamConfiguration, RunSnapshot])
def test_explicit_previous_contract_version_is_not_migrated(contract):
    if contract is ValidateRequest:
        value = request().model_dump(mode="json")
    elif contract is TeamConfiguration:
        value = {"roles": {role: {"kind": "reference" if role == "A06" else "unconfigured"}
                          for role in ("A02", "A04_A05", "A06", "A07")}}
    else:
        value = {"context": request().context.model_dump(), "profile": "test", "synthetic": True,
                 "execution_status": "waiting_review"}
    value["contract_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        contract.model_validate(value)


def test_reporter_rejects_new_item_citations_before_calling_the_model():
    req, response = role_examples()["A07"]
    first = req.validated.items[0].model_copy(update={"evidence_ids": ("invented",)})
    req = req.model_copy(update={"validated": req.validated.model_copy(update={"items": (first, *req.validated.items[1:])})})
    calls = []
    with pytest.raises(InvocationFailure) as failure:
        invoke_role("A07", SimpleNamespace(report=lambda value: calls.append(value) or response), req)
    assert failure.value.code == "UNKNOWN_CITATION" and not calls
