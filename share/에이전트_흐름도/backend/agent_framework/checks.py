"""Explicit, sequential fixture checks for a single developer-owned adapter.

Suites are local test inputs, not business requests or connectivity proofs.
All fixture files are parsed before importing a factory. Each case is called at
most once; an unknown outcome stops the suite without retrying the adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

from .composition import RoleId, TeamConfiguration, build_role
from .contracts import Contract, Identifier, Verdict
from .invocation import ROLE_CONTRACTS, InvocationFailure, invoke_role
from .ports import AdapterFailure


ErrorCode = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{1,80}$")]


class RoleCheckCase(Contract):
    id: Identifier
    input: str = Field(min_length=1)
    expected_error: ErrorCode | None = None
    expected_verdict: Verdict | None = None

    @field_validator("input")
    @classmethod
    def portable_relative_path(cls, value):
        if ("\\" in value or PurePosixPath(value).is_absolute()
                or PureWindowsPath(value).drive or PureWindowsPath(value).root):
            raise ValueError("fixture paths must be relative and use forward slashes")
        return value

    @model_validator(mode="after")
    def one_expectation(self):
        if self.expected_error is not None and self.expected_verdict is not None:
            raise ValueError("choose an error or verdict expectation")
        return self


class RoleCheckSuite(Contract):
    role: RoleId
    cases: tuple[RoleCheckCase, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def consistent_cases(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("case IDs must be unique")
        if self.role not in {"A06", "A07"} and any(case.expected_verdict for case in self.cases):
            raise ValueError("aggregate verdict expectations are available only for A06 and A07")
        return self


class CheckSuiteError(ValueError):
    """Invalid suite declarations, never an adapter execution failure."""


@dataclass(frozen=True)
class PreparedCase:
    spec: RoleCheckCase
    request: BaseModel
    path: Path
    request_sha256: str


@dataclass(frozen=True)
class PreparedSuite:
    suite: RoleCheckSuite
    cases: tuple[PreparedCase, ...]
    path: Path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("nonstandard JSON constant")


def _read_json(path):
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                       parse_constant=_invalid_constant)
    return value, sha256(raw).hexdigest()


def load_suite(path: str | Path, role: str) -> PreparedSuite:
    """Preflight every request schema before any configured code is loaded.

Expected errors describe invocation/boundary errors, not malformed JSON or
missing files. Relative fixture paths resolve from the suite's own directory.
"""
    path = Path(path).resolve()
    try:
        value, _digest = _read_json(path)
        suite = RoleCheckSuite.model_validate(value)
        if suite.role != role:
            raise ValueError("suite and selected role differ")
    except UnicodeError:
        raise
    except (ValueError, TypeError):
        raise CheckSuiteError("Invalid role check suite") from None
    request_type = ROLE_CONTRACTS[role][2]
    prepared = []
    for case in suite.cases:
        fixture = (path.parent / case.input).resolve()
        try:
            value, digest = _read_json(fixture)
            request = request_type.model_validate(value)
        except UnicodeError:
            raise
        except (ValueError, TypeError):
            raise InvocationFailure("CONTRACT_INVALID", "A suite fixture does not match the role request contract.") from None
        prepared.append(PreparedCase(case, request, fixture, digest))
    return PreparedSuite(suite, tuple(prepared), path)


def run_checks(prepared: PreparedSuite, configuration: TeamConfiguration) -> dict:
    """Initialize one role once, then check each fixture through invoke_role.

Only summaries/hashes are returned, never raw prompts, evidence or provider
exception messages. Contract acceptance does not establish semantic accuracy.
"""
    role = prepared.suite.role
    adapter = build_role(configuration, role)
    binding = configuration.roles[role]
    results = []
    stopped = False
    outputs_checked = 0
    for case in prepared.cases:
        item = {"id": case.spec.id, "request_sha256": case.request_sha256}
        if stopped:
            results.append({**item, "status": "skipped", "reason": "PRIOR_OUTCOME_UNKNOWN"})
            continue
        try:
            output = invoke_role(role, adapter, case.request,
                                 require_synthetic=binding.kind == "synthetic")
        except Exception as exc:
            structured = isinstance(exc, AdapterFailure)
            code = exc.code if structured and isinstance(exc.code, str) else "ADAPTER_FAILED"
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,80}", code):
                code = "ADAPTER_FAILED"
                structured = False
            unknown = bool(exc.outcome_unknown) if isinstance(exc, AdapterFailure) else False
            matched = structured and not unknown and case.spec.expected_error == code
            item.update(status="passed" if matched else "failed",
                        outcome="expected_rejection" if matched else "error",
                        error={"code": code,
                               "retryable": bool(exc.retryable) if isinstance(exc, AdapterFailure) else False,
                               "outcome_unknown": unknown})
            stopped = unknown
        else:
            outputs_checked += 1
            verdict = getattr(output, "verdict", None)
            matched = (case.spec.expected_error is None
                       and (case.spec.expected_verdict is None or case.spec.expected_verdict == verdict))
            serialized = json.dumps(output.model_dump(mode="json"), sort_keys=True,
                                    ensure_ascii=False, allow_nan=False).encode("utf-8")
            item.update(status="passed" if matched else "failed", outcome="accepted_output",
                        synthetic=output.synthetic, output_sha256=sha256(serialized).hexdigest())
            if verdict is not None:
                item["machine_verdict"] = verdict
            if not matched:
                item["reason"] = "EXPECTED_ERROR_NOT_RAISED" if case.spec.expected_error else "VERDICT_MISMATCH"
        results.append(item)
    counts = {status: sum(item["status"] == status for item in results)
              for status in ("passed", "failed", "skipped")}
    return {"contract_version": "2.0.0", "check": "role_contract_suite", "role": role,
            "profile": configuration.profile, "adapter_kind": binding.kind,
            "passed": counts["passed"] == len(results), "stopped_on_outcome_unknown": stopped,
            "summary": {"total": len(results), **counts, "contract_outputs_checked": outputs_checked},
            "cases": results, "connection_verified": False, "production_ready": False,
            "notice": "Fixture contract checks only; not model accuracy, source authenticity, database connectivity or authenticated approval verification."}
