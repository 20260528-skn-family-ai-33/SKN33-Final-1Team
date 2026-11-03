"""Explicit local developer configuration for independent role adapters.

Loading and describing configuration never imports a configured factory. Building
an adapter does execute trusted local code; it is not a connectivity check.
Business requests cannot select or change this configuration.
"""
from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator,model_serializer

from .contracts import Contract, ValidateRequest, ValidatedResult
from .ports import AdapterFailure, Agents
from .a02_configuration import PDFExtractionConfiguration

RoleId = Literal["A02", "A04_A05", "A06", "A07"]
BindingKind = Literal["synthetic", "reference", "factory", "unconfigured", "registered_pdf", "registered_poster"]
Scenario = Literal["pass", "fail", "review", "stale", "unavailable"]
FactoryRef = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$")]
ROLE_PORTS = {
    "A02": ("extractor", "extract"),
    "A04_A05": ("inspector", "inspect"),
    "A06": ("validator", "validate"),
    "A07": ("reporter", "report"),
}


class RoleBinding(Contract):
    kind: BindingKind
    factory: FactoryRef | None = None
    scenario: Scenario | None = None

    @model_validator(mode="after")
    def options_match_kind(self):
        if self.kind in ("factory","registered_poster") and self.factory is None:
            raise ValueError("factory binding requires module:callable")
        if self.kind not in ("factory","registered_poster") and self.factory is not None:
            raise ValueError("factory is only allowed for a factory binding")
        if self.kind != "synthetic" and self.scenario is not None:
            raise ValueError("scenario is only allowed for a synthetic binding")
        return self


class TeamConfiguration(Contract):
    profile: Literal["development", "test"] = "development"
    roles: dict[RoleId, RoleBinding]
    pdf_extraction: PDFExtractionConfiguration | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_wire_shape(self,handler):
        data=handler(self)
        if self.pdf_extraction is None:data.pop('pdf_extraction',None)
        return data

    @model_validator(mode="after")
    def complete_roles(self):
        if set(self.roles) != set(ROLE_PORTS):
            raise ValueError("configuration must explicitly declare all four roles")
        if any(binding.kind == "reference" and role != "A06" for role, binding in self.roles.items()):
            raise ValueError("reference binding is available only for A06")
        registered=[role for role,binding in self.roles.items() if binding.kind=='registered_pdf']
        if registered and registered!=['A02']:
            raise ValueError('registered_pdf is available only for A02')
        if bool(registered)!=(self.pdf_extraction is not None):
            raise ValueError('registered_pdf requires an explicit PDF extraction configuration')
        if any(role!='A04_A05' and binding.kind=='registered_poster' for role,binding in self.roles.items()):
            raise ValueError('registered_poster is only for A04_A05')
        if self.roles['A04_A05'].kind=='registered_poster' and not registered:
            raise ValueError('registered_poster requires the registered PDF baseline profile')
        return self


def _configuration(value: TeamConfiguration) -> TeamConfiguration:
    # Frozen models still contain a mutable dict; model_copy/model_construct also
    # bypass validation. Revalidate before imports or developer-code execution.
    if not isinstance(value, TeamConfiguration):
        raise ValueError("expected a TeamConfiguration")
    return TeamConfiguration.model_validate(value.model_dump(mode="python"))


def _unique_object(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("duplicate configuration key")
        values[key] = value
    return values


def _invalid_constant(_value):
    raise ValueError("configuration must contain standard JSON values")


def load_config(path: str | Path) -> TeamConfiguration:
    """Parse a local config without importing modules or creating adapters."""
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"),
                       object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    return TeamConfiguration.model_validate(value)


class _UnconfiguredRole:
    def __init__(self, role: str):
        self.role = role

    def _missing(self, _request):
        raise AdapterFailure("ADAPTER_NOT_CONFIGURED", "The selected role has no configured adapter.")

    extract = inspect = validate = report = _missing


class _SyntheticValidator:
    """Reference validation with explicit fixture provenance; no observations invented."""
    def validate(self, request: ValidateRequest) -> ValidatedResult:
        from .validation import ReferenceValidator

        result = ReferenceValidator().validate(request)
        return result.model_copy(update={"synthetic": True})


def _asynchronous(value) -> bool:
    return any(inspect.iscoroutinefunction(candidate) or inspect.isasyncgenfunction(candidate)
               for candidate in (value, getattr(value, "__call__", None)))


def _validate_adapter(adapter, role: str):
    if inspect.isclass(adapter) or isinstance(adapter, Agents) or inspect.isawaitable(adapter) or inspect.isasyncgen(adapter):
        if inspect.iscoroutine(adapter):
            adapter.close()
        raise AdapterFailure("INVALID_ROLE_ADAPTER", "A role factory must return a synchronous role adapter instance.")
    method = getattr(adapter, ROLE_PORTS[role][1], None)
    if not callable(method) or _asynchronous(method):
        raise AdapterFailure("INVALID_ROLE_ADAPTER", "The role adapter must implement its synchronous request method.")
    try:
        inspect.signature(method).bind(object())
    except (TypeError, ValueError):
        raise AdapterFailure("INVALID_ROLE_ADAPTER", "The role method must accept one request argument.") from None
    return adapter


def _from_factory(reference: str, role: str):
    module_name, name = reference.split(":", 1)
    try:
        factory = getattr(importlib.import_module(module_name), name, None)
        if not callable(factory) or _asynchronous(factory):
            raise AdapterFailure("INVALID_FACTORY", "The configured factory must be callable and synchronous.")
        try:
            inspect.signature(factory).bind()
        except (TypeError, ValueError):
            raise AdapterFailure("INVALID_FACTORY", "The configured factory must accept zero arguments.") from None
        return _validate_adapter(factory(), role)
    except AdapterFailure:
        raise
    except Exception:
        raise AdapterFailure("ADAPTER_FACTORY_FAILED", "The configured role factory could not be initialized.") from None


def _build_role(config: TeamConfiguration, role: str):
    if role not in ROLE_PORTS:
        raise ValueError("unknown framework role")
    binding = config.roles[role]
    if binding.kind in ('registered_pdf','registered_poster'):
        raise AdapterFailure('ADAPTER_CONTEXT_REQUIRED','Registered PDFs require the PostgreSQL worker and dedicated PDF storage.')
    if binding.kind == "unconfigured":
        return _UnconfiguredRole(role)
    if binding.kind == "reference":
        from .validation import ReferenceValidator

        return ReferenceValidator()
    if binding.kind == "synthetic":
        if role == "A06":
            return _SyntheticValidator()
        from .adapters.synthetic import SyntheticAdapters

        return SyntheticAdapters(scenario=binding.scenario or "pass")
    return _from_factory(binding.factory, role)


def build_role(config: TeamConfiguration, role: str):
    """Initialize only the selected role; other factories are never imported."""
    return _build_role(_configuration(config), role)


def build_agents(config: TeamConfiguration) -> Agents:
    """Explicitly initialize all roles. An error aborts assembly without fallback."""
    checked = _configuration(config)
    return Agents(**{port: _build_role(checked, role) for role, (port, _method) in ROLE_PORTS.items()})


def describe_configuration(config: TeamConfiguration) -> dict:
    """Declarations only. This function never imports or runs configured code."""
    checked = _configuration(config)
    roles = {}
    for role in ROLE_PORTS:
        binding = checked.roles[role]
        item = {"kind": binding.kind, "connection_verified": False}
        if binding.factory is not None:
            item["factory"] = binding.factory
        if binding.kind == "synthetic":
            item["scenario"] = binding.scenario or "pass"
        if binding.kind=='registered_pdf':
            item['mode']=checked.pdf_extraction.mode
            item['model']='mock-line-fixture-v1' if checked.pdf_extraction.mode=='mock' else checked.pdf_extraction.model
            item['requires_context']='PostgreSQL worker and dedicated PDF storage'
        roles[role] = item
    return {"contract_version": checked.contract_version, "profile": checked.profile,
            "connection_verified": False, "roles": roles}


def describe_agents(agents: Agents) -> dict:
    """Describe exact known adapters; a custom type is never proof of connectivity."""
    from .adapters.synthetic import SyntheticAdapters
    from .adapters.templates import QwenAdapterStub
    from .validation import ReferenceValidator

    kinds = {SyntheticAdapters: "synthetic", _SyntheticValidator: "synthetic",
             ReferenceValidator: "reference", _UnconfiguredRole: "unconfigured",
             QwenAdapterStub: "unconfigured"}
    return {role: {"kind": kinds.get(type(getattr(agents, port)), "custom"),
                   "connection_verified": False}
            for role, (port, _method) in ROLE_PORTS.items()}
