"""A07 보고 어댑터(팀 소유). A06 판정을 바꾸지 않고 팀 보고서 템플릿(template.py) 모양의 요약·할 일을 만든다.

- 조건 하나에 한 줄: 항목 이름 · 기준값 · 읽은 값(없으면 관측 상태) · 한 줄 사유 · 위치(불충족·확인 필요만) · 쪽수.
  순서는 불충족 → 확인 필요 → 충족이고, 같은 판정 안에서는 받은 순서를 지킨다.
- 원문 인용 전문·좌표·사유 코드는 요약에 쓰지 않는다. 사유는 코드 가운데 우선순위가 가장 앞선 하나를 템플릿 말로 쓴다.
  모르는 사유 코드는 버리지 않고 "기타 사유(코드)"로 남긴다(팀별 A06이 코드를 더할 수 있다).
- change_requests는 템플릿의 '고칠 것·확인할 것'이다. 한 줄 = "수정|확인 [조건 ID,…] 한 문장".
  조건 ID는 화면이 줄을 항목에 잇는 추적용이고 보고서 화면에는 보이지 않는다. 사유가 같은 '확인'은 한 줄로 묶는다.
- 항목 이름: A07 입력(ReportRequest)에는 기준 목록이 없어서 기준 문장 앞 20자를 쓴다.
  화면(frontend reportBrief.js)은 실행 기록의 presentation.label로 같은 템플릿을 다시 채운다.
- Qwen 문장 다듬기(polisher, 선택)가 성공하면 묶이지 않은 할 일 줄의 문장만 Qwen의 '할 일'로 바꾼다.
  사실 칸은 바꾸지 않고, 다듬기가 실패하면 규칙 문장만 쓴다. 연결은 팀 공용 CC_QWEN_* 설정(qwen.py).
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from backend.a07_reporter.details import facts, location_hint
from backend.a07_reporter.template import (OBSERVATION, VERDICT, action_text, groupable, limitation_text, reason_code,
                                           reason_text, short_label)
from backend.agent_framework.contracts import Evidence, ReportDraft, ReportRequest, ValidatedItem
from backend.agent_framework.ports import AdapterFailure, Reporter

ORDER = {"fail": 0, "review": 1, "pass": 2}
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
TITLE = "포스터 검수 보고서"
DRAFT_NOTICE = "자동 검수 초안입니다. 담당자가 확인하기 전에는 승인이 아닙니다."
SYNTHETIC_NOTICE = "합성 시험 데이터로 만든 보고서입니다."
POLISH_NOTICE = "할 일 문장은 Qwen이 검수 결과를 바탕으로 쓴 초안입니다."
ACTION_KIND = {"fail": "수정", "review": "확인"}


@dataclass(frozen=True)
class ItemReport:
    criterion_id: str
    verdict: str
    reasons: tuple[str, ...]                                 # 사유 코드마다 템플릿 말(Qwen 입력)
    sources: tuple[Evidence, ...]
    details: dict[str, str] = field(default_factory=dict)   # details.facts() 결과(Qwen 입력)
    criterion_text: str = ""
    label: str = ""
    required: bool = True
    expected: str | None = None
    observed: str | None = None
    observation: str = OBSERVATION["unknown"]
    location: str | None = None
    reason_code: str | None = None
    source: str | None = None                               # "PDF 1쪽" 또는 "담당자 직접 입력"


class Polisher(Protocol):
    def polish(self, reports: tuple[ItemReport, ...]) -> dict[str, tuple[str, str]] | None: ...


def _with_unit(value: str | None, unit: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    return value if not unit or value.endswith(unit) else f"{value}{unit}"


def _pages(sources: tuple[Evidence, ...]) -> str | None:
    current = [e.source for e in sources if e.kind == "current_requirement"]
    if any(source.kind == "direct_input" for source in current):
        return "담당자 직접 입력"
    pages = sorted({source.page for source in current})
    return f"PDF {'·'.join(map(str, pages))}쪽" if pages else None


def items(request: ReportRequest) -> tuple[ItemReport, ...]:
    """조건별 사실. 판정 순서(불충족 → 확인 필요 → 충족)로, 같은 판정 안에서는 받은 순서 그대로."""
    sources: dict[str, list[Evidence]] = {}
    for evidence in request.evidence.evidence:
        for criterion_id in evidence.criterion_ids:
            sources.setdefault(criterion_id, []).append(evidence)
    ordered = sorted(request.validated.items, key=lambda item: ORDER[item.verdict])
    return tuple(_report(item, tuple(sources.get(item.criterion_id, ()))) for item in ordered)


def _report(item: ValidatedItem, sources: tuple[Evidence, ...]) -> ItemReport:
    code = reason_code(item.issues) if item.verdict != "pass" else None
    observed = item.observation_status == "observed"
    return ItemReport(item.criterion_id, item.verdict, tuple(dict.fromkeys(reason_text(i) for i in item.issues)),
                      sources, _facts(item), item.criterion_text, label=short_label(item.criterion_text) or item.criterion_id,
                      required=item.required, expected=_with_unit(item.expected, item.expected_unit),
                      observed=_with_unit(item.observed, item.observed_unit) if observed else None,
                      observation=OBSERVATION.get(item.observation_status, OBSERVATION["unknown"]),
                      location=location_hint(item.image.bbox) if item.image and item.image.bbox is not None else None,
                      reason_code=code, source=_pages(sources))


def _facts(item: ValidatedItem) -> dict[str, str]:
    return facts(kind=item.kind, operator=item.operator, required=item.required, expected=item.expected,
                 expected_unit=item.expected_unit, observed=item.observed, observed_unit=item.observed_unit,
                 observation_status=item.observation_status, bbox=item.image.bbox if item.image else None)


def supplement_lines(sources: tuple[Evidence, ...]) -> list[str]:
    return [f'{EVIDENCE_KIND[e.kind]} 보완 회신: "{e.value_source.quote}" (출처: {e.value_source.reference})'
            for e in sources if e.value_source is not None]


def item_line(report: ItemReport) -> str:
    name = report.label + ("" if report.required else " (권장)")
    parts = [f"[{VERDICT[report.verdict]}] {name}", f"기준 ‘{report.expected}’" if report.expected else "기준 미정",
             f"읽은 값 ‘{report.observed}’" if report.observed else report.observation]
    if report.verdict != "pass":
        parts += [reason_text(report.reason_code)] if report.reason_code else []
        parts += [f"위치 {report.location}"] if report.location else []
    parts += [report.source] if report.source else []
    return " · ".join(dict.fromkeys(parts))   # 관측 상태와 사유가 같은 말이면 한 번만


def counts(reports: tuple[ItemReport, ...]) -> dict[str, int]:
    required = Counter(report.verdict for report in reports if report.required)
    return {"required": sum(required.values()), "pass": required["pass"], "fail": required["fail"],
            "review": required["review"], "optional": sum(not report.required for report in reports)}


def notes(request: ReportRequest, synthetic: bool, polished: bool) -> list[str]:
    out = [DRAFT_NOTICE, *([SYNTHETIC_NOTICE] if synthetic else []), *([POLISH_NOTICE] if polished else [])]
    out += [limitation_text(text) for text in request.validated.limitations]
    out += [EVIDENCE_STATUS[request.evidence.status]] if request.evidence.status in EVIDENCE_STATUS else []
    out += [limitation_text(text) for text in request.evidence.warnings]   # 모르는 경고는 그대로 옮긴다
    return list(dict.fromkeys(out))


def summary(request: ReportRequest, reports: tuple[ItemReport, ...], synthetic: bool,
            sentences: dict[str, tuple[str, str]] | None = None) -> str:
    n = counts(reports)
    lines = [TITLE, f"판정: {VERDICT[request.validated.verdict]} · 필수 조건 {n['required']}개 중 충족 {n['pass']}"
                    f" · 불충족 {n['fail']} · 확인 필요 {n['review']} · 권장 조건 {n['optional']}개"]
    lines += [item_line(report) for report in reports]
    lines += ["안내:", *(f"- {text}" for text in notes(request, synthetic, uses_sentences(reports, sentences)))]
    return "\n".join(lines)


def _groups(reports: tuple[ItemReport, ...]) -> list[list[ItemReport]]:
    """할 일 줄 묶음. 불충족은 항목마다, 사유가 같은 '확인'은 첫 항목 자리에서 한 묶음."""
    groups: dict[tuple, list[ItemReport]] = {}
    for report in reports:
        if report.verdict == "pass":
            continue
        key = (("group", report.reason_code) if groupable(report.verdict, report.reason_code)
               else ("item", report.criterion_id))
        groups.setdefault(key, []).append(report)
    return list(groups.values())


def uses_sentences(reports: tuple[ItemReport, ...], sentences: dict[str, tuple[str, str]] | None) -> bool:
    """Qwen 할 일 문장이 실제로 한 줄이라도 쓰였는지(묶인 줄은 규칙 문장을 쓴다)."""
    return any(len(members) == 1 and members[0].criterion_id in (sentences or {}) for members in _groups(reports))


def change_requests(reports: tuple[ItemReport, ...],
                    sentences: dict[str, tuple[str, str]] | None = None) -> tuple[str, ...]:
    """불충족은 '수정', 확인 필요는 '확인'. 묶이지 않은 줄은 Qwen '할 일' 문장이 있으면 그 문장을, 없으면 템플릿 규칙 문장을 쓴다."""
    out = []
    for members in _groups(reports):
        first = members[0]
        ids = ",".join(member.criterion_id for member in members)
        if len(members) == 1 and first.criterion_id in (sentences or {}):
            text = sentences[first.criterion_id][1]
        else:
            text = action_text(first.verdict, first.reason_code, "·".join(m.label for m in members), first.expected)
        out.append(f"{ACTION_KIND[first.verdict]} [{ids}] {text}")
    return tuple(out)


class TeamA07Reporter:
    """규칙 기반 보고기. 상태가 없어 여러 실행에서 같은 인스턴스를 써도 된다. polisher는 선택이다."""

    def __init__(self, polisher: Polisher | None = None):
        self.polisher = polisher

    def _sentences(self, reports: tuple[ItemReport, ...]):
        if self.polisher is None or all(report.verdict == "pass" for report in reports):
            return {},None
        try:
            sentences=self.polisher.polish(reports)
            return sentences or {},'failed' if sentences is None else None
        except AdapterFailure as error:
            return {},'outcome_unknown' if error.outcome_unknown else 'failed'
        except Exception:   # 문장 다듬기 실패가 보고를 막지 않는다(규칙 문장으로 보고)
            return {},'failed'

    def report(self, request: ReportRequest) -> ReportDraft:
        reports = items(request)
        synthetic = request.synthetic or request.validated.synthetic or request.evidence.synthetic
        cited = tuple(dict.fromkeys(e.evidence_id for report in reports for e in report.sources))
        sentences,polish_status = self._sentences(reports)
        notice=''
        if polish_status:
            notice='\n- 문장 다듬기 '+('호출 결과를 확인하지 못했습니다.' if polish_status=='outcome_unknown' else '요청을 완료하지 못했습니다.')
            notice+=' 자동 재호출 없이 검증된 판정과 근거로 규칙 기반 보고서를 제공합니다.'
        return ReportDraft(context=request.context, verdict=request.validated.verdict,
                           summary=summary(request, reports, synthetic, sentences)+notice, evidence_ids=cited,
                           change_requests=change_requests(reports, sentences), synthetic=synthetic,polish_status=polish_status)


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
