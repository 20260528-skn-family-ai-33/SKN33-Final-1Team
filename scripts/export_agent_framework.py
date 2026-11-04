"""Regenerate shared agent JSON examples, schemas and development OpenAPI offline."""
import argparse
import json
from pathlib import Path

from backend.agent_framework.adapters.generate_examples import generate, role_examples
from backend.agent_framework.api import create_app
from backend.agent_framework.contracts import CONTRACT_TYPES
from backend.agent_framework.composition import RoleBinding, TeamConfiguration, ROLE_PORTS
from backend.agent_framework.checks import RoleCheckCase, RoleCheckSuite
from backend.agent_framework.flow_checks import FlowCheckCase


def export_checks(destination: Path) -> None:
    """Portable positive and boundary-rejection fixtures, never live services."""
    checks = destination / "checks"
    checks.mkdir(parents=True, exist_ok=True)
    baseline = role_examples()
    for role in ROLE_PORTS:
        cases = []
        scenarios = ("pass", "fail", "review", "stale", "unavailable") if role in {"A06", "A07"} else ("pass",)
        for scenario in scenarios:
            request, expected = role_examples(scenario)[role]
            filename = f"{role}.{scenario}.json"
            (checks / filename).write_text(request.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
            options = {"expected_verdict": expected.verdict} if role in {"A06", "A07"} else {}
            cases.append(RoleCheckCase(id=scenario, input=filename, **options))
        request = baseline[role][0].model_dump(mode="json")
        code = None
        if role == "A02":
            request["document"]["document_version_id"] = "another-document"
            code = "DOCUMENT_VERSION_MISMATCH"
        elif role == "A04_A05":
            request["asset"]["asset_version_id"] = "another-poster"
            code = "ASSET_VERSION_MISMATCH"
        elif role == "A07":
            request["evidence"]["evidence"][0]["project_id"] = "another-project"
            code = "EVIDENCE_PROJECT_MISMATCH"
        if code is not None:
            filename = f"{role}.invalid-scope.json"
            (checks / filename).write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
            cases.append(RoleCheckCase(id="invalid-scope", input=filename, expected_error=code))
        suite = RoleCheckSuite(role=role, cases=tuple(cases))
        (checks / f"{role}.suite.json").write_text(
            json.dumps(suite.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n")


def export(destination: Path) -> None:
    for old in ("A03.json","A03.output.json","schemas/RetrieveRequest.schema.json","checks/A03.pass.json","checks/A03.invalid-scope.json","checks/A03.suite.json"):
        (destination / old).unlink(missing_ok=True)
    generate(destination)
    schemas = destination / "schemas"
    schemas.mkdir(parents=True, exist_ok=True)
    for contract in (*CONTRACT_TYPES, RoleBinding, TeamConfiguration, RoleCheckCase, RoleCheckSuite, FlowCheckCase):
        (schemas / f"{contract.__name__}.schema.json").write_text(
            json.dumps(contract.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    for name, kind in (("synthetic", "synthetic"), ("template", "unconfigured")):
        config = TeamConfiguration(roles={role: RoleBinding(kind="reference" if role == "A06" else kind)
                                         for role in ROLE_PORTS})
        (destination / f"team_config.{name}.json").write_text(
            json.dumps(config.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n")
    (destination / "openapi.json").write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    export_checks(destination)
    from backend.agent_framework.adapters.synthetic import sample_inputs
    context, document, asset = sample_inputs()
    revised, _document, revised_asset = sample_inputs("demo-run-2", "poster-v2", context.run_id)
    case = FlowCheckCase(id="synthetic-approval-rereview", context=context, document=document, asset=asset,
        revision_context=revised, revision_asset=revised_asset, expected_initial_verdict="pass",
        change_reason="합성 개발 입력: 수정 승인과 새 버전의 전체 재검수를 확인합니다.")
    (destination / "workflow.synthetic.json").write_text(case.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "examples" / "agent_framework")
    args = parser.parse_args(argv)
    export(args.output_dir)
    print(f"Exported synthetic fixtures, JSON Schema and development OpenAPI to {args.output_dir}")


if __name__ == "__main__":
    main()
