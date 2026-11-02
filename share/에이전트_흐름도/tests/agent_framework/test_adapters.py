"""Offline adapter contracts. Synthetic outcomes do not measure model quality."""
from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.agent_framework.adapters.generate_examples import generate, role_examples
from backend.agent_framework.adapters.synthetic import SCENARIOS, SyntheticAdapters, make_agents, sample_inputs
from backend.agent_framework.adapters.templates import PostgresNeo4jRetrieverStub, QwenAdapterStub
from backend.agent_framework.contracts import (
    ExtractRequest, InspectRequest, ReportRequest, RetrieveRequest,
    ValidatedItem, ValidatedResult,
)
from backend.agent_framework.ports import AdapterFailure


class AdapterContractTests(unittest.TestCase):
    def setUp(self):
        self.context, self.document, self.asset = sample_inputs()

    def extraction(self, adapters=None):
        return (adapters or SyntheticAdapters()).extract(
            ExtractRequest(context=self.context, document=self.document)
        )

    def test_synthetic_references_are_repeatable_and_revision_scoped(self):
        self.assertEqual(sample_inputs(), sample_inputs())
        next_context, next_document, next_asset = sample_inputs(
            "demo-run-2", "poster-v2", "demo-run-1"
        )
        self.assertTrue(self.document.file_id.startswith("synthetic-"))
        self.assertTrue(self.asset.file_id.startswith("synthetic-"))
        self.assertEqual(next_context.parent_run_id, self.context.run_id)
        self.assertEqual(next_document, self.document)
        self.assertNotEqual(next_asset.sha256, self.asset.sha256)
        self.assertEqual(next_context.asset_version_id, next_asset.asset_version_id)

    def test_extraction_preserves_source_quotes_and_hashes(self):
        result = self.extraction()
        self.assertTrue(result.synthetic)
        self.assertEqual([item.kind for item in result.criteria], ["text", "number"])
        self.assertEqual([item.expected for item in result.criteria], ["MORNING CUP", "3"])
        self.assertIn("합성", " ".join(result.warnings))
        for criterion in result.criteria:
            self.assertEqual(criterion.source.source_hash, sha256(criterion.source.quote.encode()).hexdigest())
            self.assertEqual(criterion.source.document_version_id, self.context.document_version_id)
            self.assertEqual(criterion.source.quote, criterion.text)

    def test_every_criterion_has_current_requirement_evidence(self):
        draft = self.extraction()
        bundle = SyntheticAdapters().retrieve(RetrieveRequest(context=self.context, criteria=draft.criteria))
        self.assertTrue(bundle.synthetic)
        self.assertEqual(bundle.status, "complete")
        self.assertEqual(bundle.postgres_revision, bundle.graph_revision)
        for criterion in draft.criteria:
            matches = [evidence for evidence in bundle.evidence if criterion.criterion_id in evidence.criterion_ids]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].kind, "current_requirement")
            self.assertEqual(matches[0].source, criterion.source)
            self.assertEqual(matches[0].project_id, self.context.project_id)

    def test_scenarios_validate_without_model_or_database_connections(self):
        expected = {"pass": "pass", "fail": "fail", "review": "review", "stale": "review", "unavailable": "review"}
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")), patch("socket.socket", side_effect=AssertionError("network forbidden")):
            for scenario in SCENARIOS:
                with self.subTest(scenario=scenario):
                    stages = role_examples(scenario)
                    for request, response in stages.values():
                        self.assertTrue(request.synthetic)
                        self.assertTrue(response.synthetic)
                        self.assertEqual(response.context, self.context)
                        self.assertEqual(type(response).model_validate_json(response.model_dump_json()), response)
                    validated = stages["A06"][1]
                    report = stages["A07"][1]
                    self.assertEqual(validated.verdict, expected[scenario])
                    self.assertEqual(report.verdict, validated.verdict)
                    self.assertIn("합성", report.summary)
                    known_ids = {evidence.evidence_id for evidence in stages["A03"][1].evidence}
                    self.assertTrue(set(report.evidence_ids).issubset(known_ids))

    def test_stale_and_unavailable_remain_explicit_retrieval_states(self):
        for scenario in ("stale", "unavailable"):
            bundle = role_examples(scenario)["A03"][1]
            self.assertEqual(bundle.status, scenario)
            if scenario == "stale":
                self.assertLess(bundle.graph_revision, bundle.postgres_revision)
                self.assertTrue(bundle.evidence)
            else:
                self.assertEqual(bundle.evidence, ())
                self.assertIsNone(bundle.graph_revision)

    def test_current_asset_provenance_is_preserved(self):
        stages = role_examples()
        for observation in stages["A04_A05"][1].observations:
            self.assertEqual(observation.image.asset_version_id, self.asset.asset_version_id)
            self.assertEqual(observation.image.asset_hash, self.asset.sha256)
            self.assertIsNotNone(observation.image.bbox)
            self.assertTrue(observation.evidence_ids)

    def test_context_mismatch_fails_instead_of_generating_another_context(self):
        altered = self.document.model_copy(update={"document_version_id": "other-document"})
        with self.assertRaises(AdapterFailure) as failure:
            SyntheticAdapters().extract(ExtractRequest(context=self.context, document=altered))
        self.assertEqual(failure.exception.code, "CONTEXT_MISMATCH")

    def test_unknown_scenario_is_not_silently_pass(self):
        with self.assertRaises(ValueError):
            SyntheticAdapters("unknown")

    def test_templates_are_unconfigured_and_never_fall_back(self):
        synthetic = SyntheticAdapters()
        extract = ExtractRequest(context=self.context, document=self.document)
        draft = synthetic.extract(extract)
        retrieve = RetrieveRequest(context=self.context, criteria=draft.criteria)
        bundle = synthetic.retrieve(retrieve)
        inspect = InspectRequest(context=self.context, criteria=draft.criteria, asset=self.asset, evidence=bundle)
        validated = ValidatedResult(context=self.context, verdict="review",
            items=tuple(ValidatedItem(criterion_id=item.criterion_id, verdict="review") for item in draft.criteria), synthetic=True)
        report = ReportRequest(context=self.context, validated=validated, evidence=bundle)
        qwen, retriever = QwenAdapterStub(), PostgresNeo4jRetrieverStub()
        with patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            for method, request in ((qwen.extract, extract), (retriever.retrieve, retrieve), (qwen.inspect, inspect), (qwen.report, report)):
                with self.subTest(method=method.__name__), self.assertRaises(AdapterFailure) as failure:
                    method(request)
                self.assertEqual(failure.exception.code, "ADAPTER_NOT_CONFIGURED")
                self.assertFalse(failure.exception.retryable)
                self.assertFalse(failure.exception.outcome_unknown)

    def test_factory_uses_the_shared_reference_validator(self):
        from backend.agent_framework.validation import ReferenceValidator
        agents = make_agents()
        self.assertIsInstance(agents.validator, ReferenceValidator)
        self.assertIs(agents.extractor, agents.inspector)
        self.assertIs(agents.inspector, agents.reporter)

    def test_team_factory_is_explicitly_unconfigured(self):
        path = Path(__file__).resolve().parents[2] / "examples" / "agent_framework" / "custom_adapter.py"
        spec = importlib.util.spec_from_file_location("team_adapter_example", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        agents = module.make_agents()
        with self.assertRaises(AdapterFailure) as failure:
            agents.extractor.extract(ExtractRequest(context=self.context, document=self.document))
        self.assertEqual(failure.exception.code, "ADAPTER_NOT_CONFIGURED")

    def test_generated_json_is_reproducible_and_every_role_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            files = generate(destination)
            before = {file.relative_to(destination): file.read_bytes() for file in files}
            self.assertEqual(len(files), 15)
            generate(destination)
            self.assertEqual(before, {file.relative_to(destination): file.read_bytes() for file in files})
            for role, (request, response) in role_examples().items():
                self.assertTrue(request.synthetic)
                self.assertEqual(type(request).model_validate_json((destination / f"{role}.json").read_text()), request)
                self.assertEqual(type(response).model_validate_json((destination / f"{role}.output.json").read_text()), response)
            self.assertTrue(json.loads((destination / "A07.output.json").read_text())["synthetic"])


if __name__ == "__main__":
    unittest.main()
