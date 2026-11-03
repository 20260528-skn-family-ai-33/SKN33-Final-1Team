"""A07 보고 어댑터(팀 소유). A06 판정을 바꾸지 않고 사람이 읽을 한국어 요약·수정 요청 초안·인용 근거를 만든다.

- 사실(조건 ID·판정·사유·기준 원문)은 코드가 정해진 틀로 쓴다. 모델·파일·네트워크를 쓰지 않는다.
- 요청에 없는 정보(조건 문장 전체, 포스터에서 읽은 값·위치)는 추측하지 않는다.
  기준 원문은 근거 묶음의 인용만 쓰고, 과거 기록·승인된 예외는 현재 기준과 구분해 표시한다.
- 판정이 나쁜 조건부터(불충족 → 확인 필요 → 통과) 보여 준다.
- 모르는 사유 코드는 버리지 않고 코드 그대로 남긴다(팀별 A06이 코드를 더할 수 있다).
- Qwen 문장 다듬기는 나중에 items()가 만든 사실 위에 붙인다. 사실 칸은 바꾸지 않는다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from backend.agent_framework.contracts import Evidence, ReportDraft, ReportRequest
from backend.agent_framework.ports import Reporter

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


@dataclass(frozen=True)
class ItemReport:
    criterion_id: str
    verdict: str
    reasons: tuple[str, ...]
    sources: tuple[Evidence, ...]


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
                            tuple(sources.get(item.criterion_id, ()))) for item in ordered)


def _source_line(evidence: Evidence) -> str:
    source = evidence.source
    return f'{EVIDENCE_KIND[evidence.kind]}: "{source.quote}" ({source.document_version_id} {source.page}쪽)'


def summary(request: ReportRequest, reports: tuple[ItemReport, ...], synthetic: bool) -> str:
    counts = Counter(report.verdict for report in reports)
    lines = [f"검수 결과: {VERDICT[request.validated.verdict]} (조건 {len(reports)}개: 불충족 {counts['fail']}"
             f" · 확인 필요 {counts['review']} · 통과 {counts['pass']})", DRAFT_NOTICE]
    if synthetic:
        lines.append(SYNTHETIC_NOTICE)
    for report in reports:
        lines.append(f"[{VERDICT[report.verdict]}] {report.criterion_id}")
        lines += [f"  {_source_line(e)}" for e in report.sources] or ["  기준 원문: 근거 묶음에 없습니다."]
        lines += [f"  사유: {text}" for text in report.reasons]
    limits = ([EVIDENCE_STATUS[request.evidence.status]] if request.evidence.status in EVIDENCE_STATUS else [])
    limits += [*request.validated.limitations, *request.evidence.warnings]   # 앞 단계의 한계 문구는 그대로 옮긴다
    if limits:
        lines += ["한계:", *(f"- {text}" for text in dict.fromkeys(limits))]
    return "\n".join(lines)


def change_requests(reports: tuple[ItemReport, ...]) -> tuple[str, ...]:
    """불충족은 수정 요청, 확인 필요는 확인 요청. 기준 원문은 현재 기준만 쓴다."""
    out = []
    for report in reports:
        if report.verdict == "pass":
            continue
        quotes = [f'"{e.source.quote}"' for e in report.sources if e.kind == "current_requirement"]
        basis = f" 기준 원문: {' / '.join(quotes)}" if quotes else " 기준 원문이 근거 묶음에 없습니다."
        why = f" 사유: {' / '.join(report.reasons)}" if report.reasons else ""
        if report.verdict == "fail":
            out.append(f"수정 요청 [{report.criterion_id}] 포스터를 기준에 맞게 고쳐 주세요.{basis}{why}")
        else:
            out.append(f"확인 요청 [{report.criterion_id}] 담당자가 포스터와 기준을 직접 확인해 주세요.{basis}{why}")
    return tuple(out)


class TeamA07Reporter:
    """규칙 기반 보고기. 상태가 없어 여러 실행에서 같은 인스턴스를 써도 된다."""

    def report(self, request: ReportRequest) -> ReportDraft:
        reports = items(request)
        synthetic = request.synthetic or request.validated.synthetic or request.evidence.synthetic
        cited = tuple(dict.fromkeys(e.evidence_id for report in reports for e in report.sources))
        return ReportDraft(context=request.context, verdict=request.validated.verdict,
                           summary=summary(request, reports, synthetic), evidence_ids=cited,
                           change_requests=change_requests(reports), synthetic=synthetic)


def create_reporter() -> Reporter:
    """설정 파일이 가리키는 factory. 모델·파일 없이 바로 동작한다."""
    return TeamA07Reporter()
