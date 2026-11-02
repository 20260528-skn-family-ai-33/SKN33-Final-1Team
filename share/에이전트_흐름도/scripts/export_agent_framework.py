"""Regenerate shared agent JSON examples, schemas and development OpenAPI offline."""
import argparse
import json
from pathlib import Path

from backend.agent_framework.adapters.generate_examples import generate
from backend.agent_framework.api import create_app
from backend.agent_framework.contracts import CONTRACT_TYPES


def export(destination: Path) -> None:
    generate(destination)
    schemas = destination / "schemas"
    schemas.mkdir(parents=True, exist_ok=True)
    for contract in CONTRACT_TYPES:
        (schemas / f"{contract.__name__}.schema.json").write_text(
            json.dumps(contract.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "openapi.json").write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "examples" / "agent_framework")
    args = parser.parse_args(argv)
    export(args.output_dir)
    print(f"Exported synthetic fixtures, JSON Schema and development OpenAPI to {args.output_dir}")


if __name__ == "__main__":
    main()
