"""Implement these interfaces without importing another agent's internals or ORM."""
from dataclasses import dataclass
from typing import Protocol

from .contracts import (CriteriaDraft, EvidenceBundle, ExtractRequest, InspectRequest,
                        InspectionResult, ReportDraft, ReportRequest, RetrieveRequest,
                        ValidateRequest, ValidatedResult)


class Extractor(Protocol):
    def extract(self, request: ExtractRequest) -> CriteriaDraft: ...


class Retriever(Protocol):
    def retrieve(self, request: RetrieveRequest) -> EvidenceBundle: ...


class Inspector(Protocol):
    def inspect(self, request: InspectRequest) -> InspectionResult: ...


class Validator(Protocol):
    def validate(self, request: ValidateRequest) -> ValidatedResult: ...


class Reporter(Protocol):
    def report(self, request: ReportRequest) -> ReportDraft: ...


@dataclass(frozen=True)
class Agents:
    extractor: Extractor
    retriever: Retriever
    inspector: Inspector
    validator: Validator
    reporter: Reporter


class AdapterFailure(Exception):
    """Safe, structured adapter error. An ambiguous remote call is never retried here."""
    def __init__(self, code: str, message: str, *, retryable=False, outcome_unknown=False):
        super().__init__(message)
        self.code, self.message = code, message
        self.retryable, self.outcome_unknown = retryable, outcome_unknown
