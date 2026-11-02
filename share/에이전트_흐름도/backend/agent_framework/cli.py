"""Explicit development commands; never starts product services or migrations."""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from pydantic import ValidationError

from .contracts import (
    CONTRACT_TYPES, Approval, CriteriaDraft, EvidenceBundle, ExtractRequest,
    FrameworkError, InspectRequest, InspectionResult, ReportDraft, ReportRequest,
    RetrieveRequest, ValidateRequest, ValidatedResult,
)
from .ports import AdapterFailure, Agents


ROLE_CONTRACTS = {
    "A02": ("extractor", "extract", ExtractRequest, CriteriaDraft),
    "A03": ("retriever", "retrieve", RetrieveRequest, EvidenceBundle),
    "A04_A05": ("inspector", "inspect", InspectRequest, InspectionResult),
    "A06": ("validator", "validate", ValidateRequest, ValidatedResult),
    "A07": ("reporter", "report", ReportRequest, ReportDraft),
}


def _emit(payload, output=None):
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if output:
        # An explicit output path authorizes this file only. Missing parent
        # directories are reported instead of silently creating another tree.
        path = Path(output)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                    prefix=".framework-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(rendered)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    else:
        sys.stdout.write(rendered)


def _error(code, message, *, retryable=False, outcome_unknown=False):
    error = FrameworkError(code=code, message=message, retryable=retryable, outcome_unknown=outcome_unknown)
    _emit({"error": error.model_dump(mode="json")})


def _factory(reference):
    if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", reference):
        raise AdapterFailure("INVALID_FACTORY", "Use an importable module:callable factory.")
    module_name, callable_name = reference.split(":", 1)
    # Loading code is permitted only by this explicit CLI option. No factory
    # name is accepted from the untrusted role request JSON.
    factory = getattr(importlib.import_module(module_name), callable_name, None)
    if not callable(factory):
        raise AdapterFailure("INVALID_FACTORY", "The named factory is not callable.")
    agents = factory()
    if not isinstance(agents, Agents):
        raise AdapterFailure("INVALID_FACTORY", "The factory must return an Agents instance.")
    return agents


def _check_role(role, request, result):
    from .validation import (validate_draft, validate_evidence, validate_inspection,
                             validate_report, validate_validated)

    try:
        if role == "A02":
            validate_draft(request.context, request.document, result)
        else:
            {"A03": validate_evidence, "A04_A05": validate_inspection,
             "A06": validate_validated, "A07": validate_report}[role](request, result)
    except ValueError as exc:
        code = str(exc).split(":", 1)[0]
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,80}", code):
            code = "BOUNDARY_INVALID"
        raise AdapterFailure(code, "The role output violates the shared scope, evidence, or verdict policy.") from None


def _has_synthetic(value):
    if isinstance(value, dict):
        return value.get("synthetic") is True or any(_has_synthetic(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_synthetic(item) for item in value)
    return False


def _role(args):
    if not args.synthetic and not args.factory:
        _error("ADAPTER_NOT_CONFIGURED", "Choose --synthetic or an explicit --factory module:callable.")
        return 2
    if args.scenario and not args.synthetic:
        _error("INVALID_ARGUMENT", "--scenario is only available with --synthetic.")
        return 2
    port_name, method_name, request_type, result_type = ROLE_CONTRACTS[args.role]
    request = request_type.model_validate_json(Path(args.input).read_text(encoding="utf-8"))
    inherited_synthetic = _has_synthetic(request.model_dump(mode="json"))
    if args.synthetic:
        from .adapters.synthetic import make_agents

        agents = make_agents(scenario=args.scenario or "pass")
        print("SYNTHETIC DEVELOPMENT FIXTURE: no real model, PostgreSQL, Neo4j, or MCP call.", file=sys.stderr)
    else:
        print("EXPLICIT DEVELOPER FACTORY: loading trusted local adapter code.", file=sys.stderr)
        agents = _factory(args.factory)
    method = getattr(getattr(agents, port_name), method_name, None)
    if not callable(method):
        raise AdapterFailure("ADAPTER_NOT_IMPLEMENTED", "The selected role does not implement its port.")
    raw = method(request)
    # model_copy/model_construct do not validate, so even typed adapter output
    # is round-tripped through the public JSON shape before accepting it.
    result = result_type.model_validate(raw.model_dump(mode="json") if isinstance(raw, result_type) else raw)
    if result.context != request.context:
        raise AdapterFailure("CONTEXT_MISMATCH", "The role changed the request's execution or version scope.")
    if args.synthetic and not result.synthetic:
        raise AdapterFailure("SYNTHETIC_LABEL_MISSING", "Fixture output must explicitly identify itself as synthetic.")
    if inherited_synthetic and not result.synthetic:
        result = result.model_copy(update={"synthetic": True})
        print("SYNTHETIC INPUT: preserving the fixture label on this role's output.", file=sys.stderr)
    _check_role(args.role, request, result)
    _emit(result.model_dump(mode="json"), args.output)
    return 0


def _demo(args):
    from .adapters.synthetic import make_agents, sample_inputs
    from .runtime import Workflow

    print("SYNTHETIC DEVELOPMENT DEMO: approvals are scripted test inputs; storage is in memory only.", file=sys.stderr)
    workflow = Workflow(make_agents(scenario="pass"), profile="development")
    context, document, asset = sample_inputs()
    steps = []

    def record(action, snapshot):
        steps.append({"action": action, "run_id": snapshot.context.run_id,
            "execution_status": snapshot.execution_status, "machine_verdict": snapshot.machine_verdict,
            "human_decision": snapshot.human_decision, "synthetic": snapshot.synthetic})
        if snapshot.error:
            raise AdapterFailure(snapshot.error.code, snapshot.error.message,
                retryable=snapshot.error.retryable, outcome_unknown=snapshot.error.outcome_unknown)
        return snapshot

    record("start", workflow.start(context, document, asset))
    record("approve_criteria", workflow.approve(context.run_id,
        Approval(context=context, actor_id=context.actor_id, decision="approve_criteria")))
    record("approve_changes", workflow.approve(context.run_id,
        Approval(context=context, actor_id=context.actor_id, decision="approve_changes",
                 reason="합성 개발 예제: 수정본 전체 재검수 흐름을 확인합니다.")))
    revised_context, _document, revised_asset = sample_inputs(
        run_id="demo-run-2", asset_version_id="poster-v2", parent_run_id=context.run_id)
    record("rereview", workflow.rereview(context.run_id, revised_context, revised_asset))
    record("approve_revised_criteria", workflow.approve(revised_context.run_id,
        Approval(context=revised_context, actor_id=revised_context.actor_id, decision="approve_criteria")))
    record("approve_final", workflow.approve(revised_context.run_id,
        Approval(context=revised_context, actor_id=revised_context.actor_id, decision="approve_final")))
    _emit({"profile": "development", "synthetic": True, "storage": "memory",
        "notice": "Scripted fixture approvals, not authenticated human decisions or real model results.",
        "steps": steps, "runs": [workflow.get(context.run_id).model_dump(mode="json"),
                                  workflow.get(revised_context.run_id).model_dump(mode="json")]}, args.output)
    return 0


def _schemas(args):
    schemas = {contract.__name__: contract.model_json_schema() for contract in CONTRACT_TYPES}
    if args.output_dir:
        directory = Path(args.output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        for name, schema in schemas.items():
            _emit(schema, directory / f"{name}.schema.json")
        _emit({"schema_count": len(schemas), "output_dir": str(directory)})
    else:
        _emit(schemas)
    return 0


def parser():
    cli = argparse.ArgumentParser(description="Creative Checker agent framework development commands")
    commands = cli.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run a labelled synthetic approval and re-review example")
    demo.add_argument("--output", help="write JSON to this file instead of stdout")
    demo.set_defaults(handler=_demo)
    role = commands.add_parser("role", help="execute one role against its JSON request contract")
    role.add_argument("role", choices=tuple(ROLE_CONTRACTS))
    role.add_argument("--input", required=True, help="request JSON file")
    role.add_argument("--output", help="response JSON file; parent directory must exist")
    adapters = role.add_mutually_exclusive_group()
    adapters.add_argument("--synthetic", action="store_true", help="explicitly use labelled development fixtures")
    adapters.add_argument("--factory", help="trusted importable module:callable returning Agents; called with no arguments")
    role.add_argument("--scenario", choices=("pass", "fail", "review", "stale", "unavailable"),
                      help="synthetic scenario name, default: pass")
    role.set_defaults(handler=_role)
    schema = commands.add_parser("schema", help="export the current versioned JSON contracts")
    schema.add_argument("--output-dir", help="write one NAME.schema.json file per contract")
    schema.set_defaults(handler=_schemas)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except ValidationError:
        _error("CONTRACT_INVALID", "Input or adapter output does not match the versioned JSON contract.")
        return 2
    except (OSError, UnicodeError):
        _error("FILE_UNAVAILABLE", "The requested input or output file could not be read or written.")
        return 2
    except AdapterFailure as exc:
        _error(exc.code, exc.message, retryable=exc.retryable, outcome_unknown=exc.outcome_unknown)
        return 1
    except (NotImplementedError, AttributeError):
        _error("ADAPTER_NOT_IMPLEMENTED", "The adapter or factory is incomplete; no successful result was produced.")
        return 1
    except Exception:
        # Do not leak model responses, credentials, local paths, or tracebacks.
        _error("ADAPTER_FAILED", "The selected command or adapter failed; inspect it in your development environment.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
