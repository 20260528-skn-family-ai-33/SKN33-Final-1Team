"""Rebuild deterministic synthetic role JSON fixtures without external services."""
from __future__ import annotations

import argparse
from pathlib import Path

from ..contracts import ExtractRequest, InspectRequest, ReportRequest, ValidateRequest
from .synthetic import SCENARIOS, make_agents, sample_inputs


def role_examples(scenario="pass"):
    context, document, asset = sample_inputs()
    agents = make_agents(scenario)
    extract_input = ExtractRequest(context=context, document=document, synthetic=True)
    criteria = agents.extractor.extract(extract_input)
    from ..context import freeze_approved_context
    evidence = freeze_approved_context(context,criteria.criteria,synthetic=True)
    if scenario in {"stale","unavailable"}:
        evidence = evidence.model_copy(update={"status":scenario, "postgres_revision": 2 if scenario=="stale" else 0, "graph_revision": 1 if scenario=="stale" else None, "evidence":() if scenario=="unavailable" else evidence.evidence})
    inspect_input = InspectRequest(context=context, criteria=criteria.criteria, asset=asset, evidence=evidence, synthetic=True)
    observations = agents.inspector.inspect(inspect_input)
    validate_input = ValidateRequest(context=context, criteria=criteria.criteria, asset=asset, evidence=evidence, inspection=observations, synthetic=True)
    validated = agents.validator.validate(validate_input)
    report_input = ReportRequest(context=context, validated=validated, evidence=evidence, synthetic=True)
    report = agents.reporter.report(report_input)
    return {
        "A02": (extract_input, criteria),
        "A04_A05": (inspect_input, observations), "A06": (validate_input, validated),
        "A07": (report_input, report),
    }


def generate(destination: Path) -> tuple[Path, ...]:
    destination.mkdir(parents=True, exist_ok=True)
    files = []
    for role, (request, response) in role_examples().items():
        for suffix, payload in (("", request), (".output", response)):
            file = destination / f"{role}{suffix}.json"
            file.write_text(payload.model_dump_json(indent=2) + "\n", encoding="utf-8")
            files.append(file)
    scenarios = destination / "scenarios"
    scenarios.mkdir(exist_ok=True)
    for scenario in SCENARIOS:
        validated = role_examples(scenario)["A06"][1]
        file = scenarios / f"{scenario}.validated.json"
        file.write_text(validated.model_dump_json(indent=2) + "\n", encoding="utf-8")
        files.append(file)
    return tuple(files)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[3] / "examples" / "agent_framework")
    args = parser.parse_args(argv)
    paths = generate(args.output)
    print(f"Generated {len(paths)} synthetic JSON examples in {args.output}")


if __name__ == "__main__":
    main()
