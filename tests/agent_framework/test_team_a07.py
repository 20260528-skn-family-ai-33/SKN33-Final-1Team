"""A07 규칙 기반 보고: 판정 보존, 팀 보고서 템플릿(report_template.json) 모양의 한 줄 결과·할 일·안내. 모델·파일·네트워크 없음."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from backend.a07_reporter.adapter import (DRAFT_NOTICE, SYNTHETIC_NOTICE, ItemReport, change_requests, create_reporter,
                                          item_line, items)
from backend.a07_reporter.prompt import prompt_items
from backend.a07_reporter.template import (ISSUE_REASON, REASON_PRIORITY, TEMPLATE, fill, particle, reason_code,
                                           reason_text)
from backend.agent_framework.checks import load_suite, run_checks
from backend.agent_framework.composition import load_config
from backend.agent_framework.contracts import CriteriaValueSource, ReportRequest
from backend.agent_framework.invocation import invoke_role

ROOT = Path(__file__).resolve().parents[2]
CHECKS = ROOT / "examples/agent_framework/checks"
LINE = re.compile(r"^(수정|확인) \[([A-Za-z0-9_.:,-]+)\] (.+)$")


def fixture(name) -> ReportRequest:
    return ReportRequest.model_validate_json((CHECKS / f"A07.{name}.json").read_text(encoding="utf-8"))


def report(request):
    return invoke_role("A07", create_reporter(), request)


def with_items(request, update):
    items_ = tuple(update(i) for i in request.validated.items)
    return request.model_copy(update={"validated": request.validated.model_copy(update={"items": items_})})


def test_shared_check_suite_passes_with_the_real_factory(monkeypatch):
    monkeypatch.delenv("CC_QWEN_BASE_URL", raising=False)
    result = run_checks(load_suite(CHECKS / "A07.suite.json", "A07"),
                        load_config(ROOT / "backend/a07_reporter/team_config.json"))
    assert result["adapter_kind"] == "factory"
    assert [(c["id"], c["status"]) for c in result["cases"]] == [
        ("pass", "passed"), ("fail", "passed"), ("review", "passed"), ("stale", "passed"),
        ("unavailable", "passed"), ("invalid-scope", "passed")]


def test_template_example_actions_and_reasons_are_reproduced():
    """팀 템플릿 poster.example의 items로 actions·reason을 그대로 만든다(이름은 example의 label)."""
    example = TEMPLATE["poster"]["example"]
    reports = tuple(ItemReport(i["criterion_id"], i["verdict"]["code"], (), (), label=i["label"], required=i["required"],
                               expected=i["expected"], observed=i["observed"], observation=i["observation"],
                               location=i["location"], source=i["source"],
                               reason_code=reason_code(i["issues"]) if i["verdict"]["code"] != "pass" else None)
                    for i in example["items"])
    assert [reason_text(r.reason_code) for r in reports] == [i["reason"] for i in example["items"]]
    lines = [LINE.match(line).groups() for line in change_requests(reports)]
    assert [(kind, text) for kind, _, text in lines] == [(a["kind"], a["text"]) for a in example["actions"]]
    assert lines[-1][1] == "c-illustration,c-file-format,c-color-mode"   # 같은 사유의 '확인'은 첫 자리에서 한 줄
    assert item_line(reports[0]) == ("[불충족] 행사 장소 · 기준 ‘고색역 상부공원 일원’ · 읽은 값 ‘고색역 상부공원’"
                                     " · 값이 기준과 다름 · 위치 아래쪽 · PDF 1쪽")
    assert item_line(reports[4]) == "[확인 필요] 우천 시 운영 안내 · 기준 미정 · 포스터에서 찾지 못함 · 기준값이 아직 정해지지 않음 · PDF 2쪽"
    assert item_line(reports[9]) == "[충족] 주요 제목 1 · 기준 ‘오늘무해!’ · 읽은 값 ‘오늘무해!’ · PDF 1쪽"   # 충족 줄엔 위치 없음


@pytest.mark.parametrize(("before", "marker", "chosen"), [
    ("행사 장소", "을(를)", "를"), ("행사명", "을(를)", "을"), ("‘고색역 상부공원 일원’", "(으)로", "으로"),
    ("‘서울’", "(으)로", "로"), ("‘11월 12일’", "(으)로", "로"), ("행사 일시", "이(가)", "가"), ("모드", "은(는)", "는"),
    ("‘3’", "(으)로", "으로"), ("‘FESTIVAL’", "(으)로", "로"), ("‘JPEG’", "와(과)", "와"), ("‘2’", "을(를)", "를"),
    ("", "을(를)", "을(를)"), ("‘★’", "이(가)", "이(가)")])
def test_particle_follows_the_last_readable_letter(before, marker, chosen):
    assert particle(before, marker) == chosen


def test_fill_uses_the_value_before_each_particle():
    rule = TEMPLATE["codes"]["action_rules"]["value_mismatch"]
    assert fill(rule, label="장소", expected="수원시청") == "장소를 ‘수원시청’으로 고쳐 주세요."


def test_failed_item_comes_first_with_values_location_and_page():
    request = fixture("fail")
    draft = report(request)
    lines = draft.summary.splitlines()
    assert lines[:2] == ["포스터 검수 보고서", "판정: 불충족 · 필수 조건 2개 중 충족 1 · 불충족 1 · 확인 필요 0 · 권장 조건 0개"]
    assert lines[2] == "[불충족] 포스터에는 딸기 3개가 표시되어야 합… · 기준 ‘3개’ · 읽은 값 ‘2개’ · 값이 기준과 다름 · 위치 가운데 · PDF 1쪽"
    assert lines[3].startswith("[충족] 포스터에는 MORNING CUP 문구… · 기준 ‘MORNING CUP’") and "위치" not in lines[3]
    assert draft.change_requests == ("수정 [requirement-number] 포스터에는 딸기 3개가 표시되어야 합…을 ‘3개’로 고쳐 주세요.",)
    quote = next(e.source.quote for e in request.evidence.evidence if "requirement-number" in e.criterion_ids)
    assert quote not in draft.summary   # 원문 인용 전문은 보고서에 쓰지 않는다(쪽수만)
    number_first = [e.evidence_id for e in request.evidence.evidence][::-1]
    assert draft.verdict == "fail" and draft.evidence_ids == tuple(number_first)


def test_review_items_with_the_same_reason_are_one_check_line_without_invented_values():
    draft = report(fixture("review"))
    line, = draft.change_requests
    kind, ids, text = LINE.match(line).groups()
    assert (kind, ids) == ("확인", "requirement-text,requirement-number")
    assert text.endswith("담당자가 직접 확인해 주세요.") and "·" in text
    assert "읽은 값" not in draft.summary   # 읽지 못한 항목에 읽은 값을 지어내지 않는다
    assert all(" · 읽을 수 없음 · " in line and line.count("읽을 수 없음") == 1
               for line in draft.summary.splitlines() if line.startswith("[확인 필요]"))


@pytest.mark.parametrize(("name", "limit"), [("stale", "최신 상태가 아닙니다"), ("unavailable", "조회하지 못했습니다")])
def test_stale_or_missing_evidence_is_stated_in_the_notes(name, limit):
    draft = report(fixture(name))
    notes = draft.summary.split("\n안내:\n", 1)[1].splitlines()
    assert draft.verdict == "review" and any(limit in note for note in notes)
    assert notes[0] == f"- {DRAFT_NOTICE}"
    if name == "unavailable":
        assert draft.evidence_ids == () and "PDF" not in draft.summary


def test_known_limitations_and_warnings_are_translated_and_unknown_ones_kept():
    request = fixture("fail")
    request = request.model_copy(update={"evidence": request.evidence.model_copy(update={"warnings": (
        "Approved-criteria context only; no PostgreSQL/Neo4j relationship lookup was executed.", "팀 경고 원문")})})
    notes = report(request).summary.split("\n안내:\n", 1)[1]
    assert "코드 검증만 했습니다. 이미지 인식 정확도와 기준 출처는 따로 검증하지 않았습니다." in notes
    assert "승인된 기준만 근거로 썼고 PostgreSQL·Neo4j 관계 조회는 하지 않았습니다." in notes
    assert "팀 경고 원문" in notes and "Code validation only" not in notes


def test_passing_report_has_no_requests_and_cites_every_source():
    request = fixture("pass")
    draft = report(request)
    assert draft.change_requests == () and draft.summary.splitlines()[1].startswith("판정: 충족 ·")
    assert set(draft.evidence_ids) == {e.evidence_id for e in request.evidence.evidence}


def test_unknown_issue_codes_are_kept_not_dropped():
    request = with_items(fixture("fail"), lambda i: i.model_copy(update={"issues": ("logo_shape_differs",)})
                         if i.verdict == "fail" else i)
    draft = report(request)
    assert "기타 사유(logo_shape_differs)" in draft.summary
    assert draft.change_requests[0].endswith("을 기준에 맞게 고쳐 주세요.")   # 모르는 사유는 기본 수정 문장


def test_optional_items_are_marked_and_kept_out_of_required_counts():
    request = with_items(fixture("fail"), lambda i: i.model_copy(update={"required": False})
                         if i.verdict == "pass" else i)
    lines = report(request).summary.splitlines()
    assert lines[1].endswith("필수 조건 1개 중 충족 0 · 불충족 1 · 확인 필요 0 · 권장 조건 1개")
    assert lines[3].startswith("[충족] 포스터에는 MORNING CUP 문구… (권장) · ")


def test_history_does_not_add_pages_and_is_still_cited():
    request = fixture("fail")
    current = next(e for e in request.evidence.evidence if "requirement-number" in e.criterion_ids)
    past = current.model_copy(update={"evidence_id": "history-1", "kind": "historical_context",
        "source": current.source.model_copy(update={"document_version_id": "past-document", "page": 7})})
    bundle = request.evidence.model_copy(update={"evidence": (*request.evidence.evidence, past)})
    draft = report(request.model_copy(update={"evidence": bundle}))
    assert "7쪽" not in draft.summary and "history-1" in draft.evidence_ids and draft.verdict == "fail"


@pytest.mark.parametrize('kind', ('current_requirement', 'historical_context', 'approved_exception'))
def test_supplement_quotes_reach_only_the_current_requirement_prompt(kind):
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
    assert quote not in draft.summary and draft.verdict == request.validated.verdict   # 템플릿 화면엔 인용 전문 없음
    sent, = prompt_items(items(revised))
    for text in (quote, supplement.reference):
        assert (text in sent.get('standard', '')) == (kind == 'current_requirement')
    if kind == 'current_requirement':
        assert original in sent['standard'] and '보완 회신:' in sent['standard']


def test_synthetic_inputs_are_labelled_and_real_inputs_are_not():
    request = fixture("pass")
    assert SYNTHETIC_NOTICE in report(request).summary
    real = request.model_copy(update={"synthetic": False,
        "validated": request.validated.model_copy(update={"synthetic": False}),
        "evidence": request.evidence.model_copy(update={"synthetic": False})})
    draft = report(real)
    assert SYNTHETIC_NOTICE not in draft.summary and draft.synthetic is False


def test_every_validator_issue_code_has_template_words_and_a_priority():
    from backend.agent_framework import validation
    emitted = {code for line in Path(validation.__file__).read_text(encoding="utf-8").splitlines() if "issue" in line
               for code in re.findall(r"['\"]([a-z]+(?:_[a-z]+)+)['\"]", line)} - {"poster_text"}
    assert len(emitted) > 15 and emitted <= set(REASON_PRIORITY)   # A06가 내는 코드는 모두 템플릿 말이 있다
    assert set(REASON_PRIORITY) == set(ISSUE_REASON) - {"_unknown"}


def test_reason_is_the_highest_priority_issue():
    issues = ("typed_attribute_review_required", "unsupported_criterion", "missing_expected_value")
    assert reason_text(reason_code(issues)) == "기준값이 아직 정해지지 않음"
    request = with_items(fixture("review"), lambda i: i.model_copy(update={"issues": issues}))
    draft = report(request)
    assert all("기준값이 아직 정해지지 않음" in line for line in draft.summary.splitlines() if line.startswith("[확인 필요]"))
    assert draft.change_requests[0].endswith("은 담당자 회신으로 기준을 정한 뒤 다시 검수해 주세요.")
