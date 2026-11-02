"""Unconfigured adapter seams. These intentionally never return synthetic results."""
from __future__ import annotations

from ..contracts import (
    CriteriaDraft, EvidenceBundle, ExtractRequest, InspectRequest,
    InspectionResult, ReportDraft, ReportRequest, RetrieveRequest,
)
from ..ports import AdapterFailure


class QwenAdapterStub:
    """Implement Qwen3.5-4B calls outside the framework; no client is created here.

    A02 must preserve PDF source provenance. A04/A05 must use the current poster
    and approved criteria. A07 must preserve the validated verdict and citations.
    Return synthetic=False only after a real configured execution.
    """

    def extract(self, request: ExtractRequest) -> CriteriaDraft:
        raise AdapterFailure("ADAPTER_NOT_CONFIGURED", "A02 Qwen PDF 추출 어댑터를 구현하고 연결해야 합니다.")

    def inspect(self, request: InspectRequest) -> InspectionResult:
        raise AdapterFailure("ADAPTER_NOT_CONFIGURED", "A04/A05 Qwen 포스터 검사 어댑터를 구현하고 연결해야 합니다.")

    def report(self, request: ReportRequest) -> ReportDraft:
        raise AdapterFailure("ADAPTER_NOT_CONFIGURED", "A07 Qwen 보고서 어댑터를 구현하고 연결해야 합니다.")


class PostgresNeo4jRetrieverStub:
    """Implement permission/version-checked PostgreSQL + Neo4j retrieval here.

    No database is opened by this class. Keep current requirements, approved
    exceptions, and historical context separate, including stale/unavailable
    states. Do not silently supply fixtures when a dependency fails.
    """

    def retrieve(self, request: RetrieveRequest) -> EvidenceBundle:
        raise AdapterFailure("ADAPTER_NOT_CONFIGURED", "A03 PostgreSQL·Neo4j 근거 검색 어댑터를 구현하고 연결해야 합니다.")

