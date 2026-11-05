"""A07 보고 어댑터(팀 소유). A06 판정을 바꾸지 않고 사람이 읽을 한국어 요약·수정 요청 초안·인용 근거를 만든다.

- 사실(조건 ID·판정·사유·기준 원문)은 코드가 정해진 틀로 쓴다. 기본은 모델·파일·네트워크를 쓰지 않는다.
- 기준값·필수 여부·검사 모델이 읽은 값·위치는 A06 검증 항목(계약 3.0.0)의 값만 쓴다(details.py).
  요청에 없는 정보는 추측하지 않는다.
  기준 원문은 근거 묶음의 인용만 쓰고, 과거 기록·승인된 예외는 현재 기준과 구분해 표시한다.
- 판정이 나쁜 조건부터(불충족 → 확인 필요 → 통과) 보여 준다.
- 모르는 사유 코드는 버리지 않고 코드 그대로 남긴다(팀별 A06이 코드를 더할 수 있다).
- Qwen 문장 다듬기(polisher, 선택)는 items()가 만든 사실 위에 불충족·확인 필요 항목의 설명·할 일 문장만 더한다.
  사실 칸은 바꾸지 않고, 다듬기가 실패하면 규칙 문장만 쓴다. 연결은 팀 공용 CC_QWEN_* 설정(qwen.py).
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from backend.a07_reporter.details import detail_lines, facts
from backend.agent_framework.contracts import Evidence, ReportDraft, ReportRequest, ValidatedItem
from backend.agent_framework.ports import AdapterFailure, Reporter

VERDICT = {"pass": "통과", "fail": "불충족", "review": "확인 필요"}
ORDER = {"fail": 0, "review": 1, "pass": 2}
ISSUE = {   # backend/agent_framework/validation.py ReferenceValidator의 사유 코드
    "value_mismatch": "포스터에서 읽은 값이 기준과 다릅니다.",
    "verdict_conflict": "검사 판정과 값 비교 결과가 서로 다릅니다.",
    "missing_observation": "포스터에서 이 항목을 찾거나 읽지 못했습니다.",
    "uncertain_observation": "검사 결과가 확실하지 않습니다.",
    "missing_citation": "검사 결과가 기준 원문을 인용하지 않았습니다.",
    "unsupported_criterion": "자동 검사로 판단할 수 없는 조건입니다.",
    "numeric_observation_invalid": "포스터에서 읽은 값을 숫자로 비교할 수 없습니다.",
    "missing_expected_value": "기준 값이 비어 있어 비교할 수 없습니다.",
    "missing_requirement_evidence": "이 조건의 현재 기준 원문이 근거 묶음에 없습니다.",
    "context_incomplete": "기준 근거가 최신이 아니거나 조회되지 않았습니다.",
    # 계약 3.0.0(A06 PR #53)에서 더해진 코드
    "unreadable_observation": "포스터에서 이 항목을 읽을 수 없었습니다.",
    "unit_mismatch": "기준과 포스터의 단위가 달라 비교할 수 없습니다.",
    "observed_absence": "포스터에 이 항목이 없습니다.",
    "observation_status_unknown": "검사 결과에 관측 상태가 적혀 있지 않습니다.",
    "observation_not_checked": "이 항목은 검사하지 않았습니다.",
    "numeric_expectation_invalid": "기준 값을 숫자로 읽을 수 없어 비교할 수 없습니다.",
    "model_review": "검사 모델이 사람 확인이 필요하다고 표시했습니다.",
}
EVIDENCE_STATUS = {
    "empty": "이 실행에는 기준 근거가 없습니다.",
    "stale": "기준 근거가 최신 상태가 아닙니다.",
    "unavailable": "기준 근거를 조회하지 못했습니다.",
}
EVIDENCE_KIND = {
    "current_requirement": "기준 원문",
    "approved_exception": "승인된 예외",
    "historical_context": "과거 기록(참고, 현재 기준 아님)",
}
DRAFT_NOTICE = "자동 검수 초안입니다. 사람이 확인하기 전에는 승인이 아닙니다."
SYNTHETIC_NOTICE = "합성 시험 데이터로 만든 보고서입니다."
POLISH_NOTICE = "설명·할 일 문장은 Qwen이 위 사실을 바탕으로 쓴 초안입니다."


@dataclass(frozen=True)
class ItemReport:
    criterion_id: str
    verdict: str
    reasons: tuple[str, ...]
    sources: tuple[Evidence, ...]
    details: dict[str, str] = field(default_factory=dict)   # details.facts() 결과
    criterion_text: str = ""


class Polisher(Protocol):
    def polish(self, reports: tuple[ItemReport, ...]) -> dict[str, tuple[str, str]] | None: ...


def reason(issue: str) -> str:
    return ISSUE.get(issue, f"기타 사유({issue})")


def items(request: ReportRequest) -> tuple[ItemReport, ...]:
    """조건별 사실. 판정 순서(불충족 → 확인 필요 → 통과)로, 같은 판정 안에서는 받은 순서 그대로."""
    sources: dict[str, list[Evidence]] = {}
    for evidence in request.evidence.evidence:
        for criterion_id in evidence.criterion_ids:
            sources.setdefault(criterion_id, []).append(evidence)
    ordered = sorted(request.validated.items, key=lambda item: ORDER[item.verdict])
    return tuple(ItemReport(item.criterion_id, item.verdict, tuple(reason(issue) for issue in item.issues),
                            tuple(sources.get(item.criterion_id, ())), _facts(item), item.criterion_text)
                 for item in ordered)


def _facts(item: ValidatedItem) -> dict[str, str]:
    return facts(kind=item.kind, operator=item.operator, required=item.required, expected=item.expected,
                 expected_unit=item.expected_unit, observed=item.observed, observed_unit=item.observed_unit,
                 observation_status=item.observation_status, bbox=item.image.bbox if item.image else None)


def _approved(report: ItemReport, indent: str) -> list[str]:
    # 기준 원문이 없을 때만 사람이 승인한 조건 문장을 보여 준다. 원문 인용은 아니다.
    return [f'{indent}승인된 조건: "{report.criterion_text}"'] if report.criterion_text else []


def _source_line(evidence: Evidence) -> str:
    source = evidence.source
    return f'{EVIDENCE_KIND[evidence.kind]}: "{source.quote}" ({source.document_version_id} {source.page}쪽)'


def supplement_lines(sources: tuple[Evidence, ...]) -> list[str]:
    return [f'{EVIDENCE_KIND[e.kind]} 보완 회신: "{e.value_source.quote}" (출처: {e.value_source.reference})'
            for e in sources if e.value_source is not None]


def summary(request: ReportRequest, reports: tuple[ItemReport, ...], synthetic: bool,
            sentences: dict[str, tuple[str, str]] | None = None) -> str:
    counts = Counter(report.verdict for report in reports)
    sentences = sentences or {}
    lines = [f"검수 결과: {VERDICT[request.validated.verdict]} (조건 {len(reports)}개: 불충족 {counts['fail']}"
             f" · 확인 필요 {counts['review']} · 통과 {counts['pass']})", DRAFT_NOTICE]
    if synthetic:
        lines.append(SYNTHETIC_NOTICE)
    if sentences:
        lines.append(POLISH_NOTICE)
    for report in reports:
        lines.append(f"[{VERDICT[report.verdict]}] {report.criterion_id}")
        lines += [f"  {_source_line(e)}" for e in report.sources] or ["  기준 원문: 근거 묶음에 없습니다.",
                                                                     *_approved(report, "  ")]
        lines += [f"  {line}" for line in supplement_lines(report.sources)]
        lines += [f"  {line}" for line in detail_lines(report.details)] if report.details else []
        lines += [f"  사유: {text}" for text in report.reasons]
        if report.criterion_id in sentences:
            explanation, action = sentences[report.criterion_id]
            lines += [f"  설명: {explanation}", f"  할 일: {action}"]
    limits = ([EVIDENCE_STATUS[request.evidence.status]] if request.evidence.status in EVIDENCE_STATUS else [])
    limits += [*request.validated.limitations, *request.evidence.warnings]   # 앞 단계의 한계 문구는 그대로 옮긴다
    if limits:
        lines += ["한계:", *(f"- {text}" for text in dict.fromkeys(limits))]
    return "\n".join(lines)


def change_requests(reports: tuple[ItemReport, ...],
                    sentences: dict[str, tuple[str, str]] | None = None) -> tuple[str, ...]:
    """불충족은 수정 요청, 확인 필요는 확인 요청. 기준 원문은 현재 기준만 쓴다.
    다듬은 문장이 있으면 할 일·설명을 그 문장으로 쓰고, 조건 ID·기준 원문은 그대로 둔다."""
    out = []
    for report in reports:
        if report.verdict == "pass":
            continue
        quotes = [f'"{e.source.quote}"' for e in report.sources if e.kind == "current_requirement"]
        basis = (f" 기준 원문: {' / '.join(quotes)}" if quotes
                 else " 기준 원문이 근거 묶음에 없습니다." + "".join(_approved(report, " ")))
        basis += "".join(f" {line}" for line in supplement_lines(
            tuple(e for e in report.sources if e.kind == "current_requirement")))
        basis += f" {'. '.join(detail_lines(report.details))}." if report.details else ""
        why = f" 사유: {' / '.join(report.reasons)}" if report.reasons else ""
        if report.criterion_id in (sentences or {}):
            explanation, action = sentences[report.criterion_id]
            head = "수정 요청" if report.verdict == "fail" else "확인 요청"
            out.append(f"{head} [{report.criterion_id}] {action}{basis} 설명: {explanation}")
        elif report.verdict == "fail":
            out.append(f"수정 요청 [{report.criterion_id}] 포스터를 기준에 맞게 고쳐 주세요.{basis}{why}")
        else:
            out.append(f"확인 요청 [{report.criterion_id}] 담당자가 포스터와 기준을 직접 확인해 주세요.{basis}{why}")
    return tuple(out)


class TeamA07Reporter:
    """규칙 기반 보고기. 상태가 없어 여러 실행에서 같은 인스턴스를 써도 된다. polisher는 선택이다."""

    def __init__(self, polisher: Polisher | None = None):
        self.polisher = polisher

    def _sentences(self, reports: tuple[ItemReport, ...]) -> dict[str, tuple[str, str]]:
        if self.polisher is None or all(report.verdict == "pass" for report in reports):
            return {}
        try:
            return self.polisher.polish(reports) or {}
        except AdapterFailure as error:
            if error.outcome_unknown:
                raise
            return {}
        except Exception:   # 문장 다듬기 실패가 보고를 막지 않는다(규칙 문장으로 보고)
            return {}

    def report(self, request: ReportRequest) -> ReportDraft:
        reports = items(request)
        synthetic = request.synthetic or request.validated.synthetic or request.evidence.synthetic
        cited = tuple(dict.fromkeys(e.evidence_id for report in reports for e in report.sources))
        sentences = self._sentences(reports)
        return ReportDraft(context=request.context, verdict=request.validated.verdict,
                           summary=summary(request, reports, synthetic, sentences), evidence_ids=cited,
                           change_requests=change_requests(reports, sentences), synthetic=synthetic)


def create_reporter() -> Reporter:
    """설정 파일이 가리키는 factory. 기본은 모델·파일 없이 규칙 문장만 쓴다.
    CC_QWEN_BASE_URL이 있으면 팀 공용 Qwen 서버로 문장을 다듬는다. A02·A04_A05와 같은 CC_QWEN_* 값을 읽는다
    (CC_QWEN_API_KEY는 선택, CC_QWEN_MODEL이 비어 있으면 Qwen/Qwen3.5-4B)."""
    base_url = os.environ.get("CC_QWEN_BASE_URL", "").strip()
    if not base_url:
        return TeamA07Reporter()
    from backend.a07_reporter.qwen import MODEL, QwenPolisher   # qwen.py가 이 모듈을 import하므로 여기서 불러온다
    return TeamA07Reporter(QwenPolisher(base_url, api_key=os.environ.get("CC_QWEN_API_KEY") or None,
                                        model=os.environ.get("CC_QWEN_MODEL", "").strip() or MODEL))
