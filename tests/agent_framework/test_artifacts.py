"""A changed producer contract must update the artifacts consumed by teammates."""
import json
from pathlib import Path

from backend.agent_framework.contracts import CONTRACT_TYPES
from scripts.export_agent_framework import export

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples" / "agent_framework"


def test_shared_contracts_examples_and_openapi_are_current(tmp_path):
    export(tmp_path)
    expected = {p.relative_to(tmp_path) for p in tmp_path.rglob("*.json")}
    actual = {p.relative_to(EXAMPLES) for p in EXAMPLES.rglob("*.json")}
    assert actual == expected, "Generated artifacts were added/removed: regenerate and review the contract change."
    for relative in sorted(expected):
        assert b"\r\n" not in (tmp_path / relative).read_bytes(), f"Non-portable generated line endings: {relative}"
        assert json.loads((tmp_path / relative).read_text(encoding="utf-8")) == json.loads(
            (EXAMPLES / relative).read_text(encoding="utf-8")), f"Stale shared contract: {relative}"


def test_all_public_schemas_reject_silent_extra_fields_and_pin_version():
    for contract in CONTRACT_TYPES:
        schema = contract.model_json_schema()
        assert schema["additionalProperties"] is False
        assert schema["properties"]["contract_version"]["const"] == "3.0.0"
