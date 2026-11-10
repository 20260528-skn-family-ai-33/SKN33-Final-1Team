from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest

from backend.agent_framework.contracts import (
    Context, SourceRef, Criterion, AssetRef, Evidence, EvidenceBundle, ImageEvidence,
    Observation, InspectionResult, ValidateRequest, ReportDraft,
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
    observation = Observation(criterion_id="count", observed="3", observation_status="observed", verdict="pass",
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


@pytest.mark.parametrize('allowance,observed,verdict,issue',[
    ('허용 범위: 공백 차이를 허용한다.','밤의 도서관','pass',None),
    ('허용 범위: 공백 차이를 허용한다.','밤의도서관','pass',None),
    ('허용 범위: 줄바꿈·공백 차이를 허용한다.','밤의\n도서관','pass',None),
    ('허용 범위: 공백 차이를 허용한다.','낮의 도서관','fail','value_mismatch'),
    ('허용 범위: 줄임말 또는 다른 표기를 허용한다.','밤의 도서관','pass',None),
    ('허용 범위: 줄임말 또는 다른 표기를 허용한다.','밤도서관','review','qualifier_interpretation_required'),
    ('허용 범위: 부제 변경을 허용한다.','낮의 도서관','review','qualifier_interpretation_required'),
    ('허용 범위: 문장 표현은 담당자 확인 후 사용할 수 있다.','밤의 도서관','review','qualifier_interpretation_required'),
    ('허용 범위: 줄바꿈·공백 차이는 허용하되 회차 숫자는 바꾸지 않는다.','낮의 도서관','fail','value_mismatch'),
    # 형식 차이와 다른 허용이 섞이면 형식 차이만 지우고, 그래도 다르면 불충족으로 단정하지 않는다.
    ('허용 범위: 공백 차이와 부제 변경을 허용한다.','밤의도서관','pass',None),
    ('허용 범위: 공백 차이와 부제 변경을 허용한다.','낮의 도서관','review','qualifier_interpretation_required'),
    # 코드가 모르는 문장은 충족·불충족의 근거가 되지 않는다.
    ('부제는 바꿔도 된다.','낮의 도서관','review','qualifier_interpretation_required'),
    # 부정문은 허용이 아니므로 공백을 지우고 비교하지 않는다.
    ('공백 차이는 허용하지 않는다.','밤의 도서관','pass',None),
    ('공백 차이는 허용하지 않는다.','밤의도서관','review','qualifier_interpretation_required'),
    # 금지 표현은 어떤 어미로 쓰여도 허용이 아니다.
    ('공백 차이는 허용할 수 없다.','밤의도서관','review','qualifier_interpretation_required'),
    ('공백 차이는 허용될 수 없다.','밤의도서관','review','qualifier_interpretation_required'),
    ('공백 차이는 허용 안됨.','밤의도서관','review','qualifier_interpretation_required'),
    ('공백 차이는 허용하면 안 된다.','밤의도서관','review','qualifier_interpretation_required'),
    ('공백 차이는 허용치 않는다.','밤의도서관','review','qualifier_interpretation_required'),
    ('공백 차이는 허용한다. 줄바꿈 차이는 허용하지 않는다.','밤의\n도서관','review','qualifier_interpretation_required'),
    # '허용'이라는 말 없이 다른 값을 받는 문장이 있으면 불충족으로 단정하지 않는다.
    ('공백 차이는 허용한다. 줄임말도 가능하다.','밤도서관','review','qualifier_interpretation_required'),
    ('공백 차이는 허용하고 부제는 바꿔도 된다.','밤도서관','review','qualifier_interpretation_required'),
    ('공백 차이는 허용한다. 부제는 변경 가능하다.','밤도서관','review','qualifier_interpretation_required'),
    ('공백 차이는 허용하되 부제는 달라도 된다.','밤도서관','review','qualifier_interpretation_required'),
    ("공백 차이는 허용한다. '밤도서관' 또는 '밤의 도서관'으로 표기한다.",'밤도서관','review','qualifier_interpretation_required'),
    ('공백 차이는 허용한다.\n줄임말을 사용할 수 있다.','밤도서관','review','qualifier_interpretation_required'),
    ('공백 차이는 허용한다. 영문 표기도 무방하다.','밤도서관','review','qualifier_interpretation_required'),
    ('공백 차이는 허용한다. 다른 표기도 인정한다.','밤도서관','review','qualifier_interpretation_required'),
    ('줄임말 또는 공백 차이는 허용한다.','밤도서관','review','qualifier_interpretation_required'),
    ('공백 차이는 허용한다. 부제는 바꿔도 되지만 제목은 유지한다.','낮의 도서관','review','qualifier_interpretation_required'),
    ('공백 차이는 허용한다. 부제는 자유롭게 표기한다.','낮의 도서관','review','qualifier_interpretation_required'),
    ('공백 차이는 허용한다. 영문으로도 표기한다.','낮의 도서관','review','qualifier_interpretation_required'),
    # 줄바꿈만 허용하면 띄어쓰기 차이는 지우지 않고, 낱말 중간 줄바꿈이 있을 수 있어 불충족도 확정하지 않는다.
    ('줄바꿈 차이는 허용한다.','밤의\n도서관','pass',None),
    ('줄바꿈 차이는 허용한다.','밤의도서관','review','qualifier_interpretation_required'),
    # 값을 지키라는 문장만 덧붙으면 여전히 불충족을 확정한다.
    ('공백 차이는 허용한다. 제목은 유지한다.','낮의 도서관','fail','value_mismatch'),
    ('공백 차이는 허용하고 부제는 바꾸지 않는다.','낮의 도서관','fail','value_mismatch'),
    # 허용을 뜻하는 다른 어미와 나열.
    ('공백 차이 허용','밤의도서관','pass',None),
    ('공백 차이는 허용할 수 있다.','밤의도서관','pass',None),
    ('공백 차이는 허용 가능하다.','밤의도서관','pass',None),
    ('공백이나 줄바꿈 차이는 허용한다.','밤의도서관','pass',None),
])
def test_allowance_is_applied_only_where_code_can_settle_it(allowance,observed,verdict,issue):
    from backend.agent_framework.contracts import RequirementDetails
    req=request();quote='제목 밤의 도서관. '+allowance
    source=SourceRef(document_version_id='d1',page=1,block_id='b1',quote=quote,source_hash=sha256(quote.encode()).hexdigest())
    value=source.model_copy(update={'quote':'밤의 도서관','source_hash':sha256('밤의 도서관'.encode()).hexdigest()})
    qualifier=source.model_copy(update={'quote':allowance,'source_hash':sha256(allowance.encode()).hexdigest()})
    criterion=req.criteria[0].model_copy(update={'text':quote,'kind':'text','operator':'contains','expected':'밤의 도서관','source':source,
        'details':RequirementDetails(target='poster_text',field='title_phrase',expected_source=value,qualifiers=(qualifier,))})
    bundle=req.evidence.model_copy(update={'evidence':(req.evidence.evidence[0].model_copy(update={'source':source}),)})
    inspection=req.inspection.model_copy(update={'observations':(req.inspection.observations[0].model_copy(update={'observed':observed}),)})
    result=ReferenceValidator().validate(req.model_copy(update={'criteria':(criterion,),'evidence':bundle,'inspection':inspection}))
    assert result.items[0].verdict==verdict
    assert (issue in result.items[0].issues) if issue else not result.items[0].issues


# 팀 샘플 의뢰서(docs/poster-baselines/2026-11-03)의 실제 허용 문장.
@pytest.mark.parametrize('sentence,rejects',[
    ('허용 범위: 줄바꿈·공백 차이는 허용한다.',True),
    ('허용 범위: 장소명 내 줄바꿈·공백 차이는 허용한다.',True),
    ('허용 범위: 공백·줄바꿈 차이는 허용한다. 연도는 2025로 유지한다.',True),
    ('허용 범위: 줄바꿈·공백과 FESTIVAL 대소문자 차이는 허용한다.',True),
    ('허용 범위: 줄바꿈·공백·두 구절 사이의 쉼표 차이는 허용하되 두 구절을 누락하지 않는다.',True),
    ('허용 범위: 공백과 &/및의 차이는 허용하되 버스킹과 영화상영회를 모두 남긴다.',False),
    ('허용 범위: 날짜 구분자와 앞자리 0의 차이는 허용한다.',False),
    ('허용 범위: 2026 또는 다른 연도 정보와 함께 해석 가능한 ‘26년’ 표기를 허용한다.',False),
    ('허용 범위: 두 사람의 이름과 역할 연결을 유지한다.',False),
    ('허용 범위: 영문 대소문자·단어 사이 공백·줄바꿈은 허용한다. 고유명·작품명 철자는 유지한다.',True),
    ('허용 범위: 줄바꿈·공백 차이는 허용하되 회차 숫자는 바꾸지 않는다.',True),
    # 모델이 요구 문장까지 함께 인용한 실제 응답(전시 v3).
    ('전시 장소는 ‘열린문화공간 후소 전시실’로 표기한다.\n허용 범위: 장소명 내 줄바꿈·공백 차이는 허용한다.',True),
    ('전시명에 ‘수원의 영화’와 ‘수원의 극장’을 모두 표기한다.\n허용 범위: 줄바꿈·공백·두 구절 사이의 쉼표 차이는 허용하되 두 구절을 누락하지 않는다.',True),
    ('허용 범위: 수요일/wed, 날짜 구분자와 0 표기는 허용한다. 날짜와 시각을 변경하지 않는다.',False),
    ('허용 범위: 날짜 구분자와 앞자리 0의 차이는 허용한다. 연도·월·일·요일 의미는 동일해야 한다.',False),
    # 무엇이 필수인지 정하는 문장은 코드가 해석하지 않으므로 값이 달라도 사람에게 넘긴다.
    ('허용 범위: 줄바꿈·공백 차이는 허용한다. 이 항목의 필수 표기는 한글 프로그램명과 작곡가명이며, 영문 부제 전체와 작품 번호는 이번 요청 범위에 포함하지 않는다.',False),
])
def test_only_format_only_source_sentences_can_confirm_a_mismatch(sentence,rejects):
    from backend.agent_framework.qualifier_rules import may_reject
    assert may_reject(sentence) is rejects


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
    forged = result.model_copy(update={"verdict": "pass", "items": (result.items[0].model_copy(update={"verdict": "pass", "issues": ()}),)})
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
    assert result.items[0].issues == (("missing_expected_value", "model_review") if model_verdict == "review"
                                      else ("missing_expected_value",))


@pytest.mark.parametrize("operator", ["equals", "contains"])
def test_custom_validator_cannot_pass_a_blank_text_expectation(operator):
    req = text_request(" ", operator)
    result = ReferenceValidator().validate(req)
    forged = result.model_copy(update={"verdict": "pass", "items": (
        result.items[0].model_copy(update={"verdict": "pass", "issues": ()}),)})
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


def test_visual_presence_requires_a_named_expectation_before_comparison():
    req = changed(request(), ["criteria", 0, "kind"], "visual")
    req = changed(req, ["criteria", 0, "operator"], "present")
    req = changed(req, ["criteria", 0, "expected"], "")
    result=ReferenceValidator().validate(req)
    assert result.verdict=='review' and 'missing_expected_value' in result.items[0].issues


@pytest.mark.parametrize("operator", ["equals", "contains"])
def test_custom_validator_cannot_fail_an_unresolved_text_expectation(operator):
    req = text_request("", operator)
    result = ReferenceValidator().validate(req)
    forged = result.model_copy(update={"verdict": "fail", "items": (
        result.items[0].model_copy(update={"verdict": "fail"}),)})
    with pytest.raises(ValueError, match="VALIDATOR_UNRESOLVED_EXPECTATION"):
        validate_validated(req, forged)
