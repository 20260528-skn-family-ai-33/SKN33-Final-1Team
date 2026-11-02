"""One synchronous contract boundary for standalone roles and workflow stages.

Inputs and outputs are copied and revalidated. Providers receive no mutable
reference to the caller's data or the baseline used for scope validation.
"""
from __future__ import annotations

import inspect
import re
from typing import Any

from pydantic import BaseModel

from .contracts import (
    CriteriaDraft, EvidenceBundle, ExtractRequest, InspectionResult,
    InspectRequest, ReportDraft, ReportRequest, RetrieveRequest,
    ValidateRequest, ValidatedResult,
)
from .ports import AdapterFailure
from .validation import (
    _criteria, ensure_context, validate_draft, validate_evidence,
    validate_inspection, validate_report, validate_report_request, validate_validated,
)


ROLE_CONTRACTS = {
    "A02": ("extractor", "extract", ExtractRequest, CriteriaDraft),
    "A03": ("retriever", "retrieve", RetrieveRequest, EvidenceBundle),
    "A04_A05": ("inspector", "inspect", InspectRequest, InspectionResult),
    "A06": ("validator", "validate", ValidateRequest, ValidatedResult),
    "A07": ("reporter", "report", ReportRequest, ReportDraft),
}


class InvocationFailure(AdapterFailure):
    """A shared boundary rejection, distinguished from provider failures."""


def _reject(code: str, message: str) -> None:
    raise InvocationFailure(code, message)


def _contract(contract: type[BaseModel], value: Any) -> BaseModel:
    try:
        if isinstance(value, BaseModel):
            if not isinstance(value, contract):
                _reject("CONTRACT_INVALID", "The value has an unexpected contract type.")
            # model_construct/model_copy and even frozen nested models can be
            # modified without validation, so no incoming BaseModel is trusted.
            value = value.model_dump(mode="python", warnings=False)
        return contract.model_validate(value).model_copy(deep=True)
    except InvocationFailure:
        raise
    except (ValueError, TypeError):
        _reject("CONTRACT_INVALID", "The value does not match the versioned JSON contract.")


def _synthetic(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("synthetic") is True or any(_synthetic(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_synthetic(item) for item in value)
    return False


def _input_policy(role: str, request: BaseModel) -> None:
    if role == "A02":
        if request.document.document_version_id != request.context.document_version_id:
            raise ValueError("DOCUMENT_VERSION_MISMATCH")
    elif role == "A03":
        _criteria(request.context, request.criteria)
    elif role in {"A04_A05", "A06"}:
        inspection_request = InspectRequest(context=request.context, criteria=request.criteria,
            asset=request.asset, evidence=request.evidence, synthetic=request.synthetic)
        # Empty observations are permitted for A04/A05; this still validates
        # criteria, retrieval provenance and the current asset before any call.
        inspection = (request.inspection if role == "A06" else
                      InspectionResult(context=request.context, observations=()))
        validate_inspection(inspection_request, inspection)
    else:
        validate_report_request(request)


def _output_policy(role: str, request: BaseModel, result: BaseModel) -> None:
    ensure_context(request.context, result)
    if role == "A02":
        validate_draft(request.context, request.document, result)
    else:
        {"A03": validate_evidence, "A04_A05": validate_inspection,
         "A06": validate_validated, "A07": validate_report}[role](request, result)


def _policy(check, *args) -> None:
    try:
        check(*args)
    except ValueError as exc:
        code = str(exc).split(":", 1)[0]
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,80}", code):
            code = "BOUNDARY_INVALID"
        _reject(code, "The role violates the shared scope, evidence, or verdict policy.")


def invoke_role(role: str, adapter: Any, request: Any, *, require_synthetic: bool = False) -> BaseModel:
    """Invoke one role once, validating both sides against an isolated baseline.

The adapter must expose the synchronous method named by ROLE_CONTRACTS. Raw
dictionary responses are supported; another role's BaseModel is not. No retry,
fallback, network request, or persistence is performed by this boundary.
"""
    if role not in ROLE_CONTRACTS:
        _reject("INVALID_ROLE", "The selected role is not part of the shared contract.")
    _port_name, method_name, request_type, result_type = ROLE_CONTRACTS[role]
    expected = _contract(request_type, request)
    _policy(_input_policy, role, expected)
    inherited_synthetic = _synthetic(expected.model_dump(mode="python"))
    method = getattr(adapter, method_name, None)
    if not callable(method):
        _reject("ADAPTER_NOT_IMPLEMENTED", "The selected role does not implement its port.")
    if (inspect.iscoroutinefunction(method) or inspect.isasyncgenfunction(method)
            or inspect.iscoroutinefunction(getattr(method, "__call__", None))
            or inspect.isasyncgenfunction(getattr(method, "__call__", None))):
        _reject("ASYNC_ADAPTER_UNSUPPORTED", "Role adapters must implement synchronous methods.")
    raw = method(expected.model_copy(deep=True))
    if inspect.isawaitable(raw) or inspect.isasyncgen(raw):
        # A synchronous wrapper can still return a coroutine. Close native
        # coroutine objects without starting them or creating a hidden loop.
        if inspect.iscoroutine(raw):
            raw.close()
        _reject("ASYNC_ADAPTER_UNSUPPORTED", "Role adapters must return a completed synchronous result.")
    result = _contract(result_type, raw)
    if require_synthetic and not result.synthetic:
        _reject("SYNTHETIC_LABEL_MISSING", "Fixture output must explicitly identify itself as synthetic.")
    if inherited_synthetic and not result.synthetic:
        result = result.model_copy(update={"synthetic": True}, deep=True)
    _policy(_output_policy, role, expected, result)
    return result.model_copy(deep=True)
