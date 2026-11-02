"""File-backed contract and evidence boundary tests, without model execution."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from experiments.document_image_benchmark import core


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "packet"
        self.root.mkdir()
        self.source_text = "필수 문구: 모닝컵. 이미지 크기는 10 x 20입니다."
        (self.root / "source.txt").write_text(self.source_text, encoding="utf-8")
        (self.root / "image.png").write_bytes(b"image fixture; no decoder is invoked")
        source_sha = hashlib.sha256((self.root / "source.txt").read_bytes()).hexdigest()
        image_sha = hashlib.sha256((self.root / "image.png").read_bytes()).hexdigest()
        self.packet = {
            "schema": "document-image-review-v1",
            "files": {"source.txt": source_sha, "image.png": image_sha},
            "cases": [{
                "id": "case-1",
                "group_id": "group-1",
                "image": {"path": "image.png", "sha256": image_sha},
                "conditions": [{
                    "id": "condition-1",
                    "level": "required",
                    "source": {
                        "path": "source.txt", "sha256": source_sha,
                        "quote": "필수 문구: 모닝컵.", "locator": "line 1",
                    },
                    "requirement": "모닝컵 문구를 포함한다.",
                    "checks": [{"kind": "text_contains", "expected": "모닝컵"}],
                }],
            }],
        }
        self.write_packet()
        self.packet_sha = hashlib.sha256((self.root / "packet.json").read_bytes()).hexdigest()
        self.observations = {
            "schema": "document-image-observations-v1",
            "packet_sha256": self.packet_sha,
            "cases": [{
                "case_id": "case-1", "image_sha256": image_sha,
                "metadata": {"width": 10, "height": 20, "format": "PNG"},
                "ocr": {"status": "success", "records": [
                    {"text": "모닝컵", "confidence": 0.99, "bbox": [0, 0, 10, 10]},
                ]},
                "visual": {"status": "success", "labels": {
                    "food_drink": {"score": 0.99, "threshold": 0.5, "prediction": 1},
                }},
            }],
        }

    def write_packet(self):
        (self.root / "packet.json").write_text(
            json.dumps(self.packet, ensure_ascii=False), encoding="utf-8"
        )

    def compare(self, checks=None, observations=None):
        packet = copy.deepcopy(self.packet)
        if checks is not None:
            packet["cases"][0]["conditions"][0]["checks"] = checks
        return core.compare(
            packet, self.observations if observations is None else observations,
            self.packet_sha,
        )["results"][0]

    def assert_execution_error(self, result):
        self.assertEqual(result["execution_status"], "error")
        self.assertIsNone(result["verdict"])

    def test_source_quote_source_hash_and_actual_file_hash_must_match(self):
        self.assertEqual(core.validate_packet(self.root), self.packet)
        original = copy.deepcopy(self.packet)
        for mutation in ("quote", "source_hash", "file_bytes"):
            with self.subTest(mutation=mutation):
                self.packet = copy.deepcopy(original)
                (self.root / "source.txt").write_text(self.source_text, encoding="utf-8")
                source = self.packet["cases"][0]["conditions"][0]["source"]
                if mutation == "quote":
                    source["quote"] = "원문에 없는 문장"
                elif mutation == "source_hash":
                    source["sha256"] = "c" * 64
                else:
                    (self.root / "source.txt").write_text("원문 변조", encoding="utf-8")
                self.write_packet()
                with self.assertRaises(ValueError):
                    core.validate_packet(self.root)

    def test_file_paths_cannot_escape_via_parent_absolute_or_symlink(self):
        outside = self.base / "outside.txt"
        outside.write_text("outside fixture", encoding="utf-8")
        outside_sha = hashlib.sha256(outside.read_bytes()).hexdigest()
        (self.root / "link.txt").symlink_to(outside)
        original = copy.deepcopy(self.packet)
        for relative in ("../outside.txt", str(outside), "link.txt"):
            with self.subTest(path=relative):
                self.packet = copy.deepcopy(original)
                self.packet["files"][relative] = outside_sha
                self.write_packet()
                with self.assertRaises(ValueError):
                    core.validate_packet(self.root)

    def test_image_source_and_delivery_require_registered_nonnull_hashes(self):
        original = copy.deepcopy(self.packet)
        for kind in ("image", "source", "delivery"):
            with self.subTest(kind=kind):
                self.packet = copy.deepcopy(original)
                case = self.packet["cases"][0]
                if kind == "image":
                    del self.packet["files"]["image.png"]
                    case["image"]["sha256"] = None
                elif kind == "source":
                    del self.packet["files"]["source.txt"]
                    case["conditions"][0]["source"]["sha256"] = None
                else:
                    case["delivery_files"] = [{
                        "name": "missing.svg", "path": "missing.svg", "sha256": None,
                    }]
                self.write_packet()
                with self.assertRaises(ValueError):
                    core.validate_packet(self.root)

    def test_duplicate_packet_case_or_condition_ids_are_rejected(self):
        original = copy.deepcopy(self.packet)
        for kind in ("case", "condition"):
            with self.subTest(kind=kind):
                self.packet = copy.deepcopy(original)
                case = self.packet["cases"][0]
                if kind == "case":
                    self.packet["cases"].append(copy.deepcopy(case))
                else:
                    case["conditions"].append(copy.deepcopy(case["conditions"][0]))
                self.write_packet()
                with self.assertRaises(ValueError):
                    core.validate_packet(self.root)

    def test_empty_low_confidence_or_disjoint_ocr_does_not_prove_absence(self):
        record_groups = [
            [],
            [{"text": "모닝컵", "confidence": 0.89}],
            [{"text": "모닝", "confidence": 0.99}, {"text": "컵", "confidence": 0.99}],
        ]
        for records in record_groups:
            with self.subTest(records=records):
                observed = copy.deepcopy(self.observations)
                observed["cases"][0]["ocr"]["records"] = records
                result = self.compare(observations=observed)
                self.assertEqual(result["execution_status"], "success")
                self.assertEqual(result["verdict"], "review")
        self.assertEqual(self.compare()["verdict"], "pass")

    def test_failed_ocr_or_missing_case_is_execution_error(self):
        for kind in ("failed", "missing"):
            with self.subTest(kind=kind):
                observed = copy.deepcopy(self.observations)
                if kind == "failed":
                    observed["cases"][0]["ocr"]["status"] = "failed"
                else:
                    observed["cases"] = []
                self.assert_execution_error(self.compare(observations=observed))

    def test_unsupported_count_remains_review_with_positive_visual_evidence(self):
        result = self.compare(checks=[
            {"kind": "visual_class", "label": "food_drink", "expected": True},
            {"kind": "unsupported", "reason": "커피 두 잔인지 확인하는 수량 조건"},
        ])
        self.assertEqual(result["execution_status"], "success")
        self.assertEqual(result["verdict"], "review")
        self.assertEqual(result["checks"][0]["verdict"], "pass")
        self.assertEqual(result["checks"][1]["verdict"], "review")

    def test_borderline_visual_is_review_and_unknown_or_invalid_visual_is_error(self):
        check = [{"kind": "visual_class", "label": "food_drink", "expected": True}]
        for mutation in ("borderline", "unknown", "nan", "infinity", "inconsistent"):
            with self.subTest(mutation=mutation):
                observed = copy.deepcopy(self.observations)
                visual = observed["cases"][0]["visual"]
                item = visual["labels"]["food_drink"]
                if mutation == "borderline":
                    item["score"] = 0.55
                elif mutation == "unknown":
                    del visual["labels"]["food_drink"]
                elif mutation == "nan":
                    item["score"] = float("nan")
                elif mutation == "infinity":
                    item["threshold"] = float("inf")
                else:
                    item["prediction"] = 0
                result = self.compare(checks=check, observations=observed)
                if mutation == "borderline":
                    self.assertEqual(result["verdict"], "review")
                else:
                    self.assert_execution_error(result)

    def test_observation_packet_hash_case_id_image_hash_and_duplicates_are_checked(self):
        for mutation in ("packet_hash", "case_id", "image_hash", "duplicate"):
            with self.subTest(mutation=mutation):
                observed = copy.deepcopy(self.observations)
                row = observed["cases"][0]
                if mutation == "packet_hash":
                    observed["packet_sha256"] = "c" * 64
                elif mutation == "case_id":
                    row["case_id"] = "unregistered-case"
                elif mutation == "image_hash":
                    row["image_sha256"] = "c" * 64
                else:
                    observed["cases"].append(copy.deepcopy(row))
                with self.assertRaises(ValueError):
                    self.compare(observations=observed)

    def test_null_metadata_ocr_and_visual_sections_produce_explicit_errors(self):
        cases = (
            ("metadata", {"kind": "image_size", "expected": [10, 20]}),
            ("ocr", {"kind": "text_contains", "expected": "모닝컵"}),
            ("visual", {"kind": "visual_class", "label": "food_drink", "expected": True}),
            ("visual_labels", {"kind": "visual_class", "label": "food_drink", "expected": True}),
            ("ocr_records", {"kind": "text_contains", "expected": "모닝컵"}),
        )
        for section, check in cases:
            with self.subTest(section=section):
                observed = copy.deepcopy(self.observations)
                row = observed["cases"][0]
                if section == "visual_labels":
                    row["visual"]["labels"] = None
                elif section == "ocr_records":
                    row["ocr"]["records"] = None
                else:
                    row[section] = None
                self.assert_execution_error(self.compare(checks=[check], observations=observed))


if __name__ == "__main__":
    unittest.main()
