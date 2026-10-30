"""Strict response and information-boundary tests; never import/load a model."""
import copy
from contextlib import nullcontext
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PIL import Image

from experiments.document_image_benchmark import qwen_judge as judge


def condition(identifier="a", checks=None):
    return {"id": identifier, "level": "required", "requirement": "컵 두 개를 오른쪽에 표시",
            "resolved_expected": {"count": 2, "position": "right"},
            "checks": checks or [{"kind": "unsupported", "expected_count": 2, "position": "right"}],
            "source": {"quote": "컵 두 개를 오른쪽에 표시", "locator": "/rules/0"}}


def response(identifier="a", verdict="pass", evidence=None, observation="오른쪽에 컵 두 개가 보입니다."):
    return {"id": identifier, "verdict": verdict, "observation": observation,
            "evidence": ["오른쪽 위 컵 한 개와 아래 컵 한 개"] if evidence is None else evidence}


class QwenJudgeTests(unittest.TestCase):
    def test_strict_json_rejects_fences_duplicate_keys_nonfinite_and_wrong_fields(self):
        valid = json.dumps({"items": [response()]}, ensure_ascii=False)
        self.assertEqual(judge.parse_response(valid, [condition()])[0]["verdict"], "pass")
        for raw in ("```json\n" + valid + "\n```", '{"items":[],"items":[]}',
                    '{"items":NaN}', '{"items":[],"extra":true}', '[]',
                    json.dumps({"items": [{**response(), "score": 1}]})):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                judge.parse_response(raw, [condition()])

    def test_ids_must_be_complete_unique_and_known_and_restore_condition_order(self):
        conditions = [condition("a"), condition("b")]
        parsed = judge.parse_response(json.dumps({"items": [response("b"), response("a")]}), conditions)
        self.assertEqual([item["id"] for item in parsed], ["a", "b"])
        for rows in ([response("a")], [response("a"), response("a")],
                     [response("a"), response("hallucinated")]):
            with self.subTest(ids=[item["id"] for item in rows]), self.assertRaises(ValueError):
                judge.parse_response(json.dumps({"items": rows}), conditions)

    def test_missing_or_unavailable_evidence_downgrades_pass_and_fail(self):
        for verdict in ("pass", "fail"):
            for item in (response(verdict=verdict, evidence=[]),
                         response(verdict=verdict, observation="  "),
                         response(verdict=verdict, evidence=["확인 불가"], observation="개수 확인 불가")):
                with self.subTest(item=item):
                    parsed = judge.parse_response(json.dumps({"items": [item]}), [condition()])
                    self.assertEqual(parsed[0]["verdict"], "review")
                    self.assertIn("downgrade", parsed[0])
        value = judge._merge(condition(), {}, {}, response())
        self.assertEqual(value["evidence"][0]["type"], "qwen_image_model_estimate")
        self.assertFalse(value["evidence"][0]["independently_verified"])

    def test_file_and_metadata_facts_use_actual_inputs_with_and_precedence(self):
        file_check = {"kind": "file_present", "expected": "editable.ai"}
        combined = condition(checks=[file_check, {"kind": "unsupported", "reason": "editability"}])
        model_item = judge.parse_response(json.dumps({"items": [response()]}), [combined])[0]
        self.assertEqual(model_item["verdict"], "review")
        missing = {"delivery_inventory_complete": True, "delivery_files": []}
        failure = judge._merge(combined, missing, {}, model_item)
        self.assertEqual(failure["verdict"], "fail")
        self.assertFalse(failure["model_estimate"])
        incomplete = judge._merge(combined, {"delivery_files": []}, {}, model_item)
        self.assertEqual(incomplete["verdict"], "review")
        pure = condition(checks=[file_check])
        present = {"delivery_files": [{"name": "editable.ai", "path": "file.ai", "sha256": "a" * 64}]}
        self.assertEqual(judge._merge(pure, present, {}, model_item)["verdict"], "pass")
        pixel = condition(checks=[{"kind": "image_size", "expected": [10, 20]}])
        self.assertEqual(judge._merge(pixel, {}, {"width": 20, "height": 10}, response())["verdict"], "fail")
        self.assertEqual(judge._merge(pixel, {}, {}, response())["execution_status"], "error")

    def test_prompt_allowlist_never_reads_or_serializes_gold_inventory_or_case_labels(self):
        class Guarded(dict):
            def __getitem__(self, key):
                if key in {"gold", "labels", "delivery_files", "delivery_inventory_complete", "expected_verdict", "checks"}:
                    raise AssertionError("Private/irrelevant field was read")
                return super().__getitem__(key)

            def get(self, key, default=None):
                if key in {"gold", "labels", "delivery_files", "delivery_inventory_complete", "expected_verdict", "checks"}:
                    raise AssertionError("Private/irrelevant field was read")
                return super().get(key, default)

        item = Guarded(copy.deepcopy(condition()), gold="SECRET_GOLD", expected_verdict="SECRET_VERDICT")
        case = Guarded(conditions=[item], gold="SECRET_GOLD", delivery_files="SECRET_INVENTORY", labels="SECRET_LABELS")
        payload = judge.condition_payload(case)
        self.assertNotIn("SECRET_", payload)
        self.assertEqual(json.loads(payload)["conditions"][0]["resolved_expected"]["count"], 2)
        self.assertNotIn("checks", json.loads(payload)["conditions"][0])
        self.assertEqual(json.loads(payload)["conditions"][0]["source"]["quote"], condition()["requirement"])

    def test_full_judge_fake_models_save_serializable_partial_and_preserve_error_accounting(self):
        class NonJsonSize:
            def __str__(self):
                return "SizeDict(shortest_edge=1024, longest_edge=2000000)"

        class FakeTensor:
            shape = (1, 10)

            def __init__(self, floating=False):
                self.floating = floating

            def __getitem__(self, index):
                return self

            def tolist(self):
                return [1, 2, 2]

            def is_floating_point(self):
                return self.floating

            def to(self, *args, **kwargs):
                return self

            def numel(self):
                return 45

        cuda = mock.Mock()
        cuda.is_available.return_value = True
        cuda.is_bf16_supported.return_value = True
        cuda.mem_get_info.return_value = (16 * 1024 ** 3, 24 * 1024 ** 3)
        cuda.get_device_name.return_value = "FAKE GPU; no device accessed"
        torch = SimpleNamespace(cuda=cuda, bfloat16="FAKE_BFLOAT16", inference_mode=nullcontext)
        prompt_calls = []

        def template(messages, **kwargs):
            self.assertIs(kwargs["enable_thinking"], False)
            prompt_calls.append(messages)
            return "template<think>\n\n</think>\n\n"

        processor = mock.Mock()
        processor.tokenizer = SimpleNamespace(padding_side="right")
        processor.image_processor = SimpleNamespace(patch_size=16, min_pixels=1024,
                                                    max_pixels=2_000_000, size=NonJsonSize())
        processor.apply_chat_template.side_effect = template
        processor.return_value = {"input_ids": FakeTensor(), "image_grid_thw": FakeTensor(),
                                  "pixel_values": FakeTensor(floating=True)}
        processor.decode.return_value = json.dumps({"items": [response("a"), response("b", verdict="review")]})
        model = mock.Mock()
        model.to.return_value = model
        model.eval.return_value = model
        model.requires_grad_.return_value = model
        fake_transformers = SimpleNamespace(AutoProcessor=mock.Mock(), Qwen3_5ForConditionalGeneration=mock.Mock())
        fake_transformers.AutoProcessor.from_pretrained.return_value = processor
        fake_transformers.Qwen3_5ForConditionalGeneration.from_pretrained.return_value = model
        probe = {"ready": True, "weight_bytes": 8_800_000_000, "issues": []}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.png"
            with Image.new("RGB", (32, 32), "white") as image:
                image.save(source)
            case = {"id": "example", "image": {"path": source.name, "sha256": judge.sha(source)},
                    "conditions": [condition("a"), condition("b")],
                    "gold": "SECRET_GOLD_NEVER_IN_PROMPT", "delivery_files": "SECRET_INVENTORY"}
            packet = {"schema": "document-image-review-v1", "cases": [case], "gold": "SECRET_PACKET_GOLD"}
            (directory / "packet.json").write_text(json.dumps(packet), encoding="utf-8")
            args = SimpleNamespace(qwen_model_dir=directory / "nonexistent-fake-model")
            for mode in ("success", "oom"):
                with self.subTest(mode=mode), \
                        mock.patch.dict(sys.modules, {"torch": torch, "transformers": fake_transformers}), \
                        mock.patch.object(judge, "preflight", return_value=probe), \
                        mock.patch("sys.stdout", new_callable=io.StringIO), \
                        mock.patch.dict(judge.os.environ):
                    model.generate.side_effect = RuntimeError("simulated OOM") if mode == "oom" else None
                    model.generate.return_value = FakeTensor()
                    output = directory / mode
                    predictions = judge.judge(args, packet, directory, output)
                    saved = json.loads((output / "predictions.partial.json").read_text(encoding="utf-8"))
                    self.assertEqual(saved, predictions)
                    self.assertEqual(saved["runtime"]["processor"]["size"], str(NonJsonSize()))
                    self.assertEqual(saved["runtime"]["completed_cases"], 1)
                    self.assertTrue(saved["runtime"]["inference_executed"])
                    self.assertFalse(saved["runtime"]["gold_passed_to_model"])
                    self.assertEqual([row["condition_id"] for row in saved["results"]], ["a", "b"])
                    self.assertTrue(all(row["execution_status"] == ("error" if mode == "oom" else "success")
                                        for row in saved["results"]))
                    if mode == "oom":
                        self.assertTrue(all(row["verdict"] is None for row in saved["results"]))
                    else:
                        raw = json.loads((output / "qwen_evidence/case_00000.json").read_text())
                        self.assertEqual(raw["profile"]["processed_dimensions"], [32, 32])
                    self.assertEqual(list(output.glob(".observations-*.tmp")), [])
        self.assertEqual(len(prompt_calls), 2)
        self.assertNotIn("SECRET_", json.dumps(prompt_calls))
        for call in model.generate.call_args_list:
            self.assertIs(call.kwargs["use_cache"], False)
            self.assertEqual(call.kwargs["max_new_tokens"], 1536)
        cuda.set_per_process_memory_fraction.assert_called_with(.48, 0)
        for call in fake_transformers.Qwen3_5ForConditionalGeneration.from_pretrained.call_args_list:
            self.assertIs(call.kwargs["local_files_only"], True)
            self.assertIs(call.kwargs["trust_remote_code"], False)
            self.assertEqual(call.kwargs["attn_implementation"], "sdpa")


if __name__ == "__main__":
    unittest.main()
