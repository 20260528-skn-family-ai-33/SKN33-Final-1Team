"""A07 규칙 기반 보고: 판정 보존, 조건별 원문·사유, 수정·확인 요청, 근거 한계 표시. 모델·파일·네트워크 없음."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from hashlib import sha256

import pytest

from backend.a07_reporter.adapter import DRAFT_NOTICE, ISSUE, SYNTHETIC_NOTICE, create_reporter, items
from backend.a07_reporter.prompt import prompt_items
from backend.agent_framework.checks import load_suite, run_checks
from backend.agent_framework.composition import load_config
from backend.agent_framework.contracts import CriteriaValueSource, ReportRequest
from backend.agent_framework.invocation import invoke_role

ROOT = Path(__file__).resolve().parents[2]
CHECKS = ROOT / "examples/agent_framework/checks"


def fixture(name) -> ReportRequest:
    return ReportRequest.model_validate_json((CHECKS / f"A07.{name}.json").read_text(encoding="utf-8"))


def report(request):
    return invoke_role("A07", create_reporter(), request)


def quote_of(request, criterion_id):
    return next(e.source.quote for e in request.evidence.evidence if criterion_id in e.criterion_ids)


def test_shared_check_suite_passes_with_the_real_factory(monkeypatch):
    monkeypatch.delenv("CC_QWEN_BASE_URL", raising=False)
    result = run_checks(load_suite(CHECKS / "A07.suite.json", "A07"),
                        load_config(ROOT / "backend/a07_reporter/team_config.json"))
    assert result["adapter_kind"] == "factory"
    assert [(c["id"], c["status"]) for c in result["cases"]] == [
        ("pass", "passed"), ("fail", "passed"), ("review", "passed"), ("stale", "passed"),
        ("unavailable", "passed"), ("invalid-scope", "passed")]


def test_failed_item_comes_first_with_its_source_and_a_change_request():
    request = fixture("fail")
    draft = report(request)
    lines = draft.summary.splitlines()
    assert lines[0] == "검수 결과: 불충족 (조건 2개: 불충족 1 · 확인 필요 0 · 통과 1)" and lines[1] == DRAFT_NOTICE
    assert lines.index("[불충족] requirement-number") < lines.index("[통과] requirement-text")
    change, = draft.change_requests
    assert change.startswith("수정 요청 [requirement-number]")
    assert quote_of(request, "requirement-number") in change and "기준과 다릅니다" in change
    at = lines.index("[불충족] requirement-number")
    assert lines[at + 2:at + 4] == ["  기준: 필수 · 숫자가 같아야 함 · 기준값 3개", "  검사 모델이 읽은 값: 2개 (포스터 가운데)"]
    assert "기준값 3개. 검사 모델이 읽은 값: 2개 (포스터 가운데). 사유:" in change
    number_first = [e.evidence_id for e in request.evidence.evidence][::-1]
    assert draft.verdict == "fail" and draft.evidence_ids == tuple(number_first)


def test_review_items_ask_for_a_human_check_with_korean_reasons():
    draft = report(fixture("review"))
    assert [c.split("]")[0] for c in draft.change_requests] == ["확인 요청 [requirement-text", "확인 요청 [requirement-number"]
    assert all("확실하지 않습니다" in c and "읽을 수 없었습니다" in c and "사람 확인이 필요하다고" in c
               and "검사 모델: 읽을 수 없음." in c for c in draft.change_requests)
    assert "검사 모델이 읽은 값" not in draft.summary   # 읽지 못한 항목에 읽은 값을 지어내지 않는다


def test_without_a_source_the_approved_criterion_is_shown_but_not_as_the_source():
    request = fixture("unavailable")
    draft = report(request)
    text = {i.criterion_id: i.criterion_text for i in request.validated.items}
    lines = draft.summary.splitlines()
    at = lines.index("[확인 필요] requirement-number")
    assert lines[at + 1:at + 3] == ["  기준 원문: 근거 묶음에 없습니다.", f'  승인된 조건: "{text["requirement-number"]}"']
    assert all("기준 원문이 근거 묶음에 없습니다. 승인된 조건:" in c for c in draft.change_requests)


@pytest.mark.parametrize(("name", "limit"), [("stale", "최신 상태가 아닙니다"), ("unavailable", "조회하지 못했습니다")])
def test_stale_or_missing_evidence_is_stated_as_a_limitation(name, limit):
    draft = report(fixture(name))
    limits = draft.summary.split("\n한계:\n", 1)[1]
    assert draft.verdict == "review" and limit in limits.splitlines()[0]
    if name == "unavailable":
        assert draft.evidence_ids == () and all("기준 원문이 근거 묶음에 없습니다" in c for c in draft.change_requests)


def test_passing_report_has_no_requests_and_cites_every_source():
    request = fixture("pass")
    draft = report(request)
    assert draft.change_requests == () and draft.summary.startswith("검수 결과: 통과")
    assert set(draft.evidence_ids) == {e.evidence_id for e in request.evidence.evidence}


def test_unknown_issue_codes_are_kept_not_dropped():
    request = fixture("fail")
    items = tuple(i.model_copy(update={"issues": (*i.issues, "logo_shape_differs")}) if i.verdict == "fail" else i
                  for i in request.validated.items)
    draft = report(request.model_copy(update={"validated": request.validated.model_copy(update={"items": items})}))
    assert "기타 사유(logo_shape_differs)" in draft.summary and "기타 사유(logo_shape_differs)" in draft.change_requests[0]


def test_history_is_labelled_and_never_used_as_the_current_requirement():
    request = fixture("fail")
    current = next(e for e in request.evidence.evidence if "requirement-number" in e.criterion_ids)
    past = current.model_copy(update={"evidence_id": "history-1", "kind": "historical_context",
        "source": current.source.model_copy(update={"document_version_id": "past-document"})})
    bundle = request.evidence.model_copy(update={"evidence": (*request.evidence.evidence, past)})
    draft = report(request.model_copy(update={"evidence": bundle}))
    assert f'과거 기록(참고, 현재 기준 아님): "{current.source.quote}" (past-document 1쪽)' in draft.summary
    assert draft.change_requests[0].count(current.source.quote) == 1 and "history-1" in draft.evidence_ids
    assert draft.verdict == "fail"


@pytest.mark.parametrize('kind', ('current_requirement', 'historical_context', 'approved_exception'))
def test_supplement_quotes_and_references_reach_report_and_current_requirement_prompt(kind):
    request = fixture('fail')
    current = next(e for e in request.evidence.evidence if 'requirement-number' in e.criterion_ids)
    original = '표시 개수는 발주처 확인 중입니다.'
    quote = '확정 회신: 딸기는 3개를 표시합니다.'
    supplement = CriteriaValueSource(record_id='reply-1', purpose='synthetic_test', reference='client-reply-20261105',
        quote=quote, quote_sha256=sha256(quote.encode()).hexdigest(), submitted_by=request.context.actor_id,
        submitted_at=datetime(2026, 11, 5, tzinfo=timezone.utc), synthetic=True)
    amended = current.model_copy(update={'kind': kind, 'value_source': supplement,
        'evidence_id': current.evidence_id if kind == 'current_requirement' else 'supplement-' + kind,
        'approval_id': 'approval-1' if kind == 'approved_exception' else None,
        'applicable_asset_version_id': request.context.asset_version_id if kind == 'approved_exception' else None,
        'source': current.source.model_copy(update={'quote': original, 'source_hash': sha256(original.encode()).hexdigest()})})
    evidence = (tuple(amended if e == current else e for e in request.evidence.evidence)
                if kind == 'current_requirement' else (*request.evidence.evidence, amended))
    revised = request.model_copy(update={'evidence': request.evidence.model_copy(update={'evidence': evidence})})
    draft = report(revised)
    assert original in draft.summary and quote in draft.summary and supplement.reference in draft.summary
    assert '보완 회신:' in draft.summary and draft.verdict == request.validated.verdict
    sent, = prompt_items(items(revised))
    for text in (quote, supplement.reference):
        assert (text in draft.change_requests[0]) == (kind == 'current_requirement')
        assert (text in sent.get('standard', '')) == (kind == 'current_requirement')
    if kind == 'current_requirement':
        assert original in draft.change_requests[0] and original in sent['standard']
        assert '보완 회신:' in sent['standard']


def test_synthetic_inputs_are_labelled_and_real_inputs_are_not():
    request = fixture("pass")
    assert SYNTHETIC_NOTICE in report(request).summary
    real = request.model_copy(update={"synthetic": False,
        "validated": request.validated.model_copy(update={"synthetic": False}),
        "evidence": request.evidence.model_copy(update={"synthetic": False})})
    draft = report(real)
    assert SYNTHETIC_NOTICE not in draft.summary and draft.synthetic is False


V3_ISSUES = ("unreadable_observation", "unit_mismatch", "observed_absence", "observation_status_unknown",
             "observation_not_checked", "numeric_expectation_invalid", "model_review",
             "typed_attribute_review_required", "qualifier_interpretation_required",
             "normalized_value_review_required", "date_comparison_review_required")


def test_issue_codes_added_by_contract_3_read_as_korean_sentences():
    request = fixture("review")
    items = tuple(i.model_copy(update={"issues": V3_ISSUES}) for i in request.validated.items)
    draft = report(request.model_copy(update={"validated": request.validated.model_copy(update={"items": items})}))
    assert "기타 사유" not in draft.summary
    assert all(ISSUE[code] in draft.summary and ISSUE[code] in draft.change_requests[0] for code in V3_ISSUES)


def test_visual_conflict_is_explained_as_inconsistent_presence_not_a_value_comparison():
    request=fixture('review')
    item=request.validated.items[0].model_copy(update={'kind':'visual','issues':('verdict_conflict',)})
    request=request.model_copy(update={'validated':request.validated.model_copy(update={'items':(item,*request.validated.items[1:])})})
    draft=report(request)
    message='시각 요소의 존재·부재 보고와 검사 판정이 서로 맞지 않습니다.'
    assert draft.verdict=='review' and message in draft.summary and message in draft.change_requests[0]
    assert message in str(prompt_items(items(request)))
