"""Implement these interfaces without importing another agent's internals or ORM."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TYPE_CHECKING

from .contracts import (CriteriaDraft, ExtractRequest, InspectRequest,
                        InspectionResult, ReportDraft, ReportRequest,
                        ValidateRequest, ValidatedResult)
from .contracts import Context, DocumentRef, AssetRef

if TYPE_CHECKING:
    from .files import VerifiedDocument, VerifiedPoster


class FileAccess(Protocol):
    def read_document(self, context: Context, reference: DocumentRef) -> VerifiedDocument: ...
    def read_asset(self, context: Context, reference: AssetRef) -> VerifiedPoster: ...


class Extractor(Protocol):
    def extract(self, request: ExtractRequest) -> CriteriaDraft: ...


class Inspector(Protocol):
    def inspect(self, request: InspectRequest) -> InspectionResult: ...


class Validator(Protocol):
    def validate(self, request: ValidateRequest) -> ValidatedResult: ...


class Reporter(Protocol):
    def report(self, request: ReportRequest) -> ReportDraft: ...


@dataclass(frozen=True)
class Agents:
    extractor: Extractor
    inspector: Inspector
    validator: Validator
    reporter: Reporter


class AdapterFailure(Exception):
    """Safe, structured adapter error. An ambiguous remote call is never retried here."""
    def __init__(self, code: str, message: str, *, retryable=False, outcome_unknown=False):
        super().__init__(message)
        self.code, self.message = code, message
        self.retryable, self.outcome_unknown = retryable, outcome_unknown
