"""Deterministic protocol fixtures. These never inspect a real PDF or poster.

The fixture file IDs and hashes describe synthetic marker strings, not files.
Only an explicit choice of this adapter enables synthetic behavior; production
adapter failures must never fall back to this module.
"""
from __future__ import annotations

import hashlib
from typing import Literal

from ..contracts import (
    AssetRef, Context, CriteriaDraft, Criterion, DocumentRef, Evidence,
    EvidenceBundle, ExtractRequest, ImageEvidence, InspectRequest,
    InspectionResult, Observation, ReportDraft, ReportRequest,
    SourceRef,
)
from ..ports import AdapterFailure, Agents

Scenario = Literal["pass", "fail", "review", "stale", "unavailable"]
SCENARIOS = ("pass", "fail", "review", "stale", "unavailable")
SYNTHETIC_NOTICE = "합성 계약 예제입니다. 실제 PDF·포스터·모델·DB를 읽거나 실행하지 않았습니다."
DOCUMENT_MARKER = "SYNTHETIC DOCUMENT REFERENCE; no PDF bytes exist; document-v1"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sample_inputs(
    run_id: str = "demo-run-1",
    asset_version_id: str = "poster-v1",
    parent_run_id: str | None = None,
) -> tuple[Context, DocumentRef, AssetRef]:
    """Return clearly synthetic references, without opening or creating files."""
    context = Context(
        project_id="synthetic-project", run_id=run_id, actor_id="synthetic-reviewer",
        document_version_id="document-v1", criteria_version_id="criteria-v1",
        asset_version_id=asset_version_id, parent_run_id=parent_run_id,
    )
    document = DocumentRef(
        file_id="synthetic-pdf-reference", document_version_id=context.document_version_id,
        sha256=_hash(DOCUMENT_MARKER), page_count=1,
    )
    asset = AssetRef(
        file_id=f"synthetic-{asset_version_id}", asset_version_id=asset_version_id,
        sha256=_hash(f"SYNTHETIC POSTER REFERENCE; no image bytes exist; {asset_version_id}"),
        width=1200, height=1600,
    )
    return context, document, asset


def _same_context(left: Context, right: Context) -> None:
    if left != right:
        raise AdapterFailure("CONTEXT_MISMATCH", "합성 예제의 실행 컨텍스트가 일치하지 않습니다.")


class SyntheticAdapters:
    """One stateless fixture implements A02, A04/A05 and A07 ports."""

    def __init__(self, scenario: Scenario = "pass"):
        if scenario not in SCENARIOS:
            raise ValueError(f"scenario must be one of {', '.join(SCENARIOS)}")
        self.scenario = scenario

    def extract(self, request: ExtractRequest) -> CriteriaDraft:
        if request.context.document_version_id != request.document.document_version_id:
            raise AdapterFailure("CONTEXT_MISMATCH", "문서 버전이 추출 실행 컨텍스트와 다릅니다.")
        text_quote = "포스터에는 MORNING CUP 문구를 정확히 표시해야 합니다."
        number_quote = "포스터에는 딸기 3개가 표시되어야 합니다."

        def source(block_id: str, quote: str) -> SourceRef:
            return SourceRef(
                document_version_id=request.document.document_version_id,
                page=1, block_id=block_id, quote=quote, source_hash=_hash(quote),
            )

        return CriteriaDraft(
            context=request.context,
            criteria=(
                Criterion(
                    criterion_id="requirement-text", text=text_quote, kind="text",
                    expected="MORNING CUP", operator="equals",
                    source=source("synthetic-page-1-block-1", text_quote),
                ),
                Criterion(
                    criterion_id="requirement-number", text=number_quote, kind="number",
                    expected="3", operator="equals", unit="개",
                    source=source("synthetic-page-1-block-2", number_quote),
                ),
            ),
            warnings=(SYNTHETIC_NOTICE, "파일 해시는 실제 파일이 아닌 합성 참조 문자열의 SHA-256입니다."),
            synthetic=True,
        )

    def inspect(self, request: InspectRequest) -> InspectionResult:
        _same_context(request.context, request.evidence.context)
        if request.context.asset_version_id != request.asset.asset_version_id:
            raise AdapterFailure("CONTEXT_MISMATCH", "포스터 버전이 검사 실행 컨텍스트와 다릅니다.")
        observations = []
        for index, criterion in enumerate(request.criteria):
            citations = tuple(
                evidence.evidence_id for evidence in request.evidence.evidence
                if evidence.kind == "current_requirement" and criterion.criterion_id in evidence.criterion_ids
            )
            observed, verdict, uncertainty = criterion.expected, "pass", None
            if criterion.kind == "unsupported" or criterion.operator == "manual":
                observed, verdict, uncertainty = "", "review", "합성 예제: 수동 확인 조건입니다."
            elif self.scenario == "review":
                observed, verdict, uncertainty = "", "review", "합성 예제: 현재 포스터의 관측이 불명확합니다."
            elif self.scenario == "fail" and criterion.kind == "number":
                observed, verdict = "2", "fail"
            observations.append(Observation(
                criterion_id=criterion.criterion_id, observed=observed, verdict=verdict,
                image=ImageEvidence(
                    asset_version_id=request.asset.asset_version_id, asset_hash=request.asset.sha256,
                    bbox=(0.08, 0.08, 0.88, 0.24) if index % 2 == 0 else (0.12, 0.35, 0.78, 0.82),
                ),
                evidence_ids=citations, uncertainty=uncertainty,
            ))
        return InspectionResult(context=request.context, observations=tuple(observations), synthetic=True)

    def report(self, request: ReportRequest) -> ReportDraft:
        _same_context(request.context, request.validated.context)
        _same_context(request.context, request.evidence.context)
        verdict = request.validated.verdict
        validated_ids = {item.criterion_id for item in request.validated.items}
        citations = tuple(
            evidence.evidence_id for evidence in request.evidence.evidence
            if evidence.kind == "current_requirement" and validated_ids.intersection(evidence.criterion_ids)
        )
        lines = [SYNTHETIC_NOTICE, f"기계 판정: {verdict}"]
        changes = []
        for item in request.validated.items:
            issues = ", ".join(item.issues)
            lines.append(f"{item.criterion_id}: {item.verdict}" + (f" ({issues})" if issues else ""))
            if item.verdict != "pass":
                action = "수정 확인" if item.verdict == "fail" else "근거와 관측 확인"
                changes.append(f"{item.criterion_id}: {action}이 필요합니다.")
        return ReportDraft(
            context=request.context, verdict=verdict, summary="\n".join(lines),
            evidence_ids=citations, change_requests=tuple(changes), synthetic=True,
        )


def make_agents(scenario: Scenario = "pass") -> Agents:
    """Explicit synthetic composition; validation is the shared reference code."""
    from ..validation import ReferenceValidator

    adapters = SyntheticAdapters(scenario)
    return Agents(
        extractor=adapters, inspector=adapters,
        validator=ReferenceValidator(), reporter=adapters,
    )

