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
from backend.app.config import ConfigurationError
from backend.app.persistence.database import PersistenceError

from .contracts import (
    CONTRACT_TYPES, Approval, FrameworkError,
)
from .ports import AdapterFailure, Agents
from .invocation import ROLE_CONTRACTS, InvocationFailure, invoke_role
from .checks import CheckSuiteError
from .flow_checks import FlowCaseError


class _ConfigError(ValueError):
    pass


def _load_configuration(path):
    from .composition import load_config
    try:
        return load_config(path)
    except (ValueError, TypeError) as exc:
        raise _ConfigError("Invalid team configuration") from exc


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


def _role(args):
    if not args.synthetic and not args.factory and not args.config:
        _error("ADAPTER_NOT_CONFIGURED", "Choose --synthetic, --config, or an explicit --factory module:callable.")
        return 2
    if args.scenario and not args.synthetic:
        _error("INVALID_ARGUMENT", "--scenario is only available with --synthetic.")
        return 2
    port_name, _method_name, request_type, _result_type = ROLE_CONTRACTS[args.role]
    request = request_type.model_validate_json(Path(args.input).read_text(encoding="utf-8"))
    require_synthetic = args.synthetic
    if args.synthetic:
        from .adapters.synthetic import make_agents

        agents = make_agents(scenario=args.scenario or "pass")
        adapter = getattr(agents, port_name)
        print("SYNTHETIC DEVELOPMENT FIXTURE: no real model, PostgreSQL, Neo4j, or MCP call.", file=sys.stderr)
    elif args.config:
        from .composition import build_role
        configuration = _load_configuration(args.config)
        adapter = build_role(configuration, args.role)
        require_synthetic = configuration.roles[args.role].kind == "synthetic"
        print("EXPLICIT ROLE CONFIGURATION: initializing the selected role only.", file=sys.stderr)
    else:
        print("EXPLICIT DEVELOPER FACTORY: loading trusted local adapter code.", file=sys.stderr)
        agents = _factory(args.factory)
        adapter = getattr(agents, port_name)
    result = invoke_role(args.role, adapter, request, require_synthetic=require_synthetic)
    if result.synthetic:
        print("SYNTHETIC INPUT/OUTPUT: fixture provenance is preserved through the shared boundary.", file=sys.stderr)
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
    contracts = CONTRACT_TYPES
    if args.include_configuration:
        from .composition import RoleBinding, TeamConfiguration
        contracts = (*contracts, RoleBinding, TeamConfiguration)
    if args.include_checks:
        from .checks import RoleCheckCase, RoleCheckSuite
        from .flow_checks import FlowCheckCase
        contracts = (*contracts, RoleCheckCase, RoleCheckSuite, FlowCheckCase)
    schemas = {contract.__name__: contract.model_json_schema() for contract in contracts}
    if args.output_dir:
        directory = Path(args.output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        for name, schema in schemas.items():
            _emit(schema, directory / f"{name}.schema.json")
        _emit({"schema_count": len(schemas), "output_dir": str(directory)})
    else:
        _emit(schemas)
    return 0


def _doctor(args):
    from .composition import describe_configuration
    configuration = _load_configuration(args.config)
    _emit({**describe_configuration(configuration), "check": "configuration_only",
           "factory_imports_performed": False, "role_calls_performed": False,
           "notice": "Declarations only; adapter import, model, database and file connectivity are not verified."}, args.output)
    return 0


def _check(args):
    from .checks import load_suite, run_checks

    configuration = _load_configuration(args.config)
    prepared = load_suite(args.cases, args.role)
    protected = {prepared.path, Path(args.config).resolve(), *(case.path for case in prepared.cases)}
    if args.output and Path(args.output).resolve() in protected:
        _error("INVALID_ARGUMENT", "The report must not overwrite its configuration, suite or request fixtures.")
        return 2
    print("EXPLICIT ROLE CHECK: initializes only the selected adapter; each fixture is invoked once, without retries.", file=sys.stderr)
    report = run_checks(prepared, configuration)
    # A failed check is itself a valid report. Preflight/setup errors use the
    # normal error path and leave a previous report untouched.
    _emit(report, args.output)
    return 0 if report["passed"] else 1


def _serve(args):
    from .api import create_app
    import uvicorn
    configuration = _load_configuration(args.config)
    print("DEVELOPMENT API: loopback only, no authentication, memory-only state; factories load trusted local code.", file=sys.stderr)
    app = create_app(configuration=configuration)
    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, reload=False)
    return 0


def _check_flow(args):
    from .flow_checks import load_flow_case, run_flow_check

    configuration = _load_configuration(args.config)
    case = load_flow_case(args.case)
    protected = {Path(args.config).resolve(), Path(args.case).resolve()}
    if args.output:
        output = Path(args.output)
        if output.resolve() in protected or (output.exists() and any(output.samefile(p) for p in protected)):
            _error("INVALID_ARGUMENT", "The report must not overwrite its configuration or workflow fixture.")
            return 2
        if not output.parent.is_dir() or output.is_dir():
            _error("FILE_UNAVAILABLE", "The output parent must exist and the report must be a file.")
            return 2
    print("EXPLICIT WORKFLOW CHECK: initializes all configured roles; approvals are scripted test inputs. "
          "Adapters run once per stage, without retries.", file=sys.stderr)
    report = run_flow_check(case, configuration)
    _emit(report, args.output)
    return 0 if report["passed"] else 1


def _port(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


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
    adapters.add_argument("--config", help="trusted team configuration JSON; initializes only the selected role")
    role.add_argument("--scenario", choices=("pass", "fail", "review", "stale", "unavailable"),
                      help="synthetic scenario name, default: pass")
    role.set_defaults(handler=_role)
    schema = commands.add_parser("schema", help="export the current versioned JSON contracts")
    schema.add_argument("--output-dir", help="write one NAME.schema.json file per contract")
    schema.add_argument("--include-configuration", action="store_true", help="also include role binding and team configuration schemas")
    schema.add_argument("--include-checks", action="store_true", help="also include role check suite schemas")
    schema.set_defaults(handler=_schemas)
    doctor = commands.add_parser("doctor", help="validate and describe role configuration without loading any factory")
    doctor.add_argument("--config", required=True, help="team configuration JSON")
    doctor.add_argument("--output", help="write diagnostics JSON; parent directory must exist")
    doctor.set_defaults(handler=_doctor)
    check = commands.add_parser("check", help="check one configured role against a local suite of request fixtures")
    check.add_argument("role", choices=tuple(ROLE_CONTRACTS))
    check.add_argument("--config", required=True, help="trusted team configuration; only the selected role is initialized")
    check.add_argument("--cases", required=True, help="suite JSON; fixture paths are relative to this file")
    check.add_argument("--output", help="write summary JSON, including failed checks; parent directory must exist")
    check.set_defaults(handler=_check)
    flow = commands.add_parser("check-flow", help="rehearse configured criteria approval, review, report and re-review")
    flow.add_argument("--config", required=True, help="trusted team configuration; initializes all roles")
    flow.add_argument("--case", required=True, help="workflow fixture JSON with original/revised file references and expected verdicts")
    flow.add_argument("--output", help="write a summary report, including failures; parent directory must exist")
    flow.set_defaults(handler=_check_flow)
    serve = commands.add_parser("serve", help="start the configured development API on loopback; never starts product services")
    serve.add_argument("--config", required=True, help="explicit trusted team configuration JSON")
    serve.add_argument("--port", type=_port, default=8012)
    serve.set_defaults(handler=_serve)
    from .postgres_commands import postgres_check,postgres_serve,postgres_worker,postgres_migrate
    for name,handler,help_text in (
        ('postgres-check',postgres_check,'read-only existing M008 and AF001 extension preflight'),
        ('postgres-serve',postgres_serve,'start authenticated PostgreSQL framework API; never executes models'),
        ('worker',postgres_worker,'run the separate fenced framework role worker'),
        ('migrate-postgres',postgres_migrate,'explicit owner-only additive AF001 migration in an existing M008 DB')):
        command=commands.add_parser(name,help=help_text)
        command.add_argument('--env-file',required=True,help='explicit app-only env file; migration uses a separate owner-only file')
        if name in ('postgres-serve','worker'):
            command.add_argument('--config',required=True,help='fixed trusted team adapter configuration')
        if name=='postgres-serve':command.add_argument('--port',type=_port,default=8012)
        if name=='worker':command.add_argument('--once',action='store_true',help='recover expired claims and execute at most one queued phase')
        command.set_defaults(handler=handler)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except _ConfigError:
        _error("CONFIG_INVALID", "Team configuration must declare all roles and use supported role settings.")
        return 2
    except CheckSuiteError:
        _error("CHECK_SUITE_INVALID", "The suite must declare the selected role, unique cases and supported expectations.")
        return 2
    except FlowCaseError:
        _error("FLOW_CASE_INVALID", "The workflow fixture must use fixed document/criteria and a new child run/poster version.")
        return 2
    except ConfigurationError:
        _error('POSTGRES_CONFIGURATION_INVALID','Use an explicit app-only or migration-only environment file for the selected action.')
        return 2
    except PersistenceError as exc:
        _error(exc.code,'The PostgreSQL operation was not confirmed. Inspect readiness before any explicit retry.',outcome_unknown=exc.outcome=='commit_unknown')
        return 1
    except ValidationError:
        _error("CONTRACT_INVALID", "Input or adapter output does not match the versioned JSON contract.")
        return 2
    except (OSError, UnicodeError):
        _error("FILE_UNAVAILABLE", "The requested input or output file could not be read or written.")
        return 2
    except AdapterFailure as exc:
        _error(exc.code, exc.message, retryable=exc.retryable, outcome_unknown=exc.outcome_unknown)
        return 2 if isinstance(exc, InvocationFailure) and exc.code == "CONTRACT_INVALID" else 1
    except (NotImplementedError, AttributeError):
        _error("ADAPTER_NOT_IMPLEMENTED", "The adapter or factory is incomplete; no successful result was produced.")
        return 1
    except Exception:
        # Do not leak model responses, credentials, local paths, or tracebacks.
        _error("ADAPTER_FAILED", "The selected command or adapter failed; inspect it in your development environment.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
