"""Independent team-owned role factories; intentionally no real model or DB I/O.

Each zero-argument factory returns one adapter, not an Agents collection. Keep
imports and constructors free of network/database/model loading. A teammate may
create a client lazily inside the role method after implementing its contract.
Instances can be shared between runs: guard mutable state and client concurrency.
Until implemented, these seams raise ADAPTER_NOT_CONFIGURED and never substitute
synthetic fixtures. Real implementations must preserve nested synthetic flags.
"""
from __future__ import annotations

from backend.agent_framework.adapters.templates import (
    PostgresNeo4jRetrieverStub,
    QwenAdapterStub,
)
from backend.agent_framework.contracts import (
    CriteriaDraft,
    EvidenceBundle,
    ExtractRequest,
    InspectRequest,
    InspectionResult,
    ReportDraft,
    ReportRequest,
    RetrieveRequest,
)
from backend.agent_framework.ports import Extractor, Inspector, Reporter, Retriever, Validator
from backend.agent_framework.validation import ReferenceValidator


class TeamPDFExtractor(QwenAdapterStub):
    """A02 owner: implement PDF-to-criteria extraction with source provenance."""

    def extract(self, request: ExtractRequest) -> CriteriaDraft:
        # Replace this body with your implementation; preserve the request scope.
        return super().extract(request)


class TeamEvidenceRetriever(PostgresNeo4jRetrieverStub):
    """A03 owner: implement PostgreSQL/Neo4j evidence retrieval and freshness."""

    def retrieve(self, request: RetrieveRequest) -> EvidenceBundle:
        # Keep permission/version filters and unavailable results explicit.
        return super().retrieve(request)


class TeamPosterInspector(QwenAdapterStub):
    """A04/A05 owner: inspect this poster version against all approved criteria."""

    def inspect(self, request: InspectRequest) -> InspectionResult:
        # Historical evidence must not be presented as current observations.
        return super().inspect(request)


class TeamReportWriter(QwenAdapterStub):
    """A07 owner: explain the validated verdict without changing that verdict."""

    def report(self, request: ReportRequest) -> ReportDraft:
        # Preserve validated citations and limitations in the returned report.
        return super().report(request)


def create_extractor() -> Extractor:
    return TeamPDFExtractor()


def create_retriever() -> Retriever:
    return TeamEvidenceRetriever()


def create_inspector() -> Inspector:
    return TeamPosterInspector()


def create_validator() -> Validator:
    """Reference code validation only; no model or source-authenticity proof."""
    return ReferenceValidator()


def create_reporter() -> Reporter:
    return TeamReportWriter()
