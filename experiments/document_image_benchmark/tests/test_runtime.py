"""Observer boundary tests using image fixtures and owned-process fakes only."""
import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from PIL import Image

from experiments.document_image_benchmark import runtime


class RuntimeTests(unittest.TestCase):
    def test_records_reject_out_of_bounds_and_keep_coordinates_and_confidence(self):
        valid = {"rec_texts": ["  안내  "], "rec_scores": [.91],
                 "rec_polys": [[[1, 2], [9, 2], [9, 8], [1, 8]]]}
        result = runtime._records({"res": valid}, 10, 10)
        self.assertEqual(result[0]["text"], "안내")
        self.assertEqual(result[0]["bbox_xyxy"], [1, 2, 9, 8])
        self.assertEqual(result[0]["confidence"], .91)
        self.assertEqual(result[0]["recognition_engine"], "paddleocr")
        for mutate in (
            lambda value: value["rec_polys"][0][0].__setitem__(0, -1),
            lambda value: value["rec_polys"][0][0].__setitem__(1, 11),
            lambda value: value["rec_scores"].__setitem__(0, float("nan")),
            lambda value: value["rec_scores"].clear(),
        ):
            bad = copy.deepcopy(valid)
            mutate(bad)
            with self.assertRaises(ValueError):
                runtime._records(bad, 10, 10)
        empty = {"rec_texts": [], "rec_scores": [], "rec_polys": []}
        self.assertEqual(runtime._records(empty, 10, 10), [])

    def test_failed_observations_are_null_not_an_empty_success(self):
        for stage, key in (("ocr", "records"), ("visual", "labels")):
            with self.subTest(stage=stage):
                result = runtime._failed(stage, ValueError("invalid source"))
                self.assertEqual(result["status"], "error")
                self.assertIsNone(result[key])
                self.assertEqual(result["error"]["type"], "ValueError")
                self.assertIn("invalid source", result["error"]["message"])

    def test_image_preserves_original_format_and_applies_exif_coordinates(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, normalized = directory / "source.jpg", directory / "oriented.png"
            with Image.new("RGB", (30, 20), (50, 100, 150)) as image:
                exif = Image.Exif()
                exif[274] = 6  # Rotate 90 degrees clockwise for displayed dimensions.
                image.save(source, exif=exif)
            original_hash = runtime.sha(source)
            metadata = runtime._image(source, normalized)
            self.assertEqual(metadata["format"], "JPEG")
            self.assertEqual(metadata["original_dimensions"], [30, 20])
            self.assertEqual(metadata["oriented_dimensions"], [20, 30])
            self.assertEqual((metadata["width"], metadata["height"]), (20, 30))
            self.assertEqual(metadata["exif_orientation"], 6)
            self.assertEqual(runtime.sha(source), original_hash)
            with Image.open(normalized) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (20, 30))
                self.assertEqual(image.mode, "RGB")
                self.assertNotIn(274, image.getexif())

    def test_paddle_child_uses_cpu_snapshot_and_only_stops_its_owned_process(self):
        owned = mock.Mock()
        owned.stdin = io.BytesIO()
        owned.stdout = io.BytesIO()
        owned.poll.return_value = None
        owned.wait.side_effect = [subprocess.TimeoutExpired("owned-child", 10), 0]
        unrelated = mock.Mock()
        args = SimpleNamespace(repo=Path("/snapshot/dependencies"),
                               ocr_python=Path("/existing/ocr/bin/python"),
                               ocr_models=Path("/existing/models"))
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(runtime.subprocess, "Popen", return_value=owned) as popen, \
                mock.patch.object(runtime._PaddleChild, "receive",
                                  return_value={"ok": True, "event": "ready", "device": "cpu"}), \
                mock.patch.object(runtime.os, "kill", side_effect=AssertionError("No external PID signaling")):
            child = runtime._PaddleChild(args, Path(temporary))
            command = popen.call_args.args[0]
            self.assertEqual(command[:2], [str(args.ocr_python),
                             str(args.repo / "experiments/poster_benchmark/paddle_service.py")])
            self.assertEqual(command[2:4], ["--device", "cpu"])
            self.assertEqual(popen.call_args.kwargs["cwd"], str(args.repo))
            env = popen.call_args.kwargs["env"]
            self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "")
            self.assertEqual(env["HF_HUB_OFFLINE"], "1")
            child.close()
            owned.terminate.assert_called_once_with()
            owned.kill.assert_called_once_with()
            self.assertEqual(owned.wait.call_count, 2)
            self.assertTrue(owned.stdin.closed)
            self.assertTrue(owned.stdout.closed)
            self.assertTrue(child.log.closed)
            unrelated.terminate.assert_not_called()
            unrelated.kill.assert_not_called()

    def test_observe_checkpoints_every_case_without_reading_private_conditions(self):
        class GuardedCase(dict):
            def __getitem__(self, key):
                if key in {"conditions", "gold", "delivery_files", "group_id"}:
                    raise AssertionError("Observer read private condition information")
                return super().__getitem__(key)

            def get(self, key, default=None):
                if key in {"conditions", "gold", "delivery_files", "group_id"}:
                    raise AssertionError("Observer read private condition information")
                return super().get(key, default)

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            packet_root = directory / "packet"
            packet_root.mkdir()
            source = packet_root / "image.png"
            with Image.new("RGB", (12, 8), "white") as image:
                image.save(source)
            cases = [GuardedCase(id="valid", image={"path": "image.png", "sha256": runtime.sha(source)},
                                 conditions="SECRET_CONDITION"),
                     GuardedCase(id="invalid", image={"path": "image.png", "sha256": "0" * 64},
                                 gold="SECRET_GOLD")]
            packet = {"schema": "document-image-review-v1", "cases": cases}
            (packet_root / "packet.json").write_text(json.dumps(packet), encoding="utf-8")
            output = directory / "output"
            probe = {"visual_ready": False, "ocr_ready": False, "issues": ["test missing runtime"]}
            with mock.patch.object(runtime, "preflight", return_value=probe), \
                    mock.patch.object(runtime, "_PaddleChild", side_effect=AssertionError("No real child")), \
                    mock.patch.object(runtime, "_checkpoint", wraps=runtime._checkpoint) as checkpoint, \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as stdout, \
                    mock.patch.dict(runtime.os.environ):
                observations = runtime.observe(SimpleNamespace(), packet, packet_root, output)
            partial_text = (output / "observations.partial.json").read_text(encoding="utf-8")
            self.assertEqual(json.loads(partial_text), observations)
            self.assertEqual(checkpoint.call_count, 4)  # Initial, each case, then cleanup.
            self.assertEqual(observations["runtime"]["observed_case_count"], 2)
            self.assertEqual(observations["runtime"]["status"], "completed")
            self.assertFalse(observations["runtime"]["inference_executed"])
            self.assertEqual(len(observations["cases"]), 2)
            self.assertIsNone(observations["cases"][1]["metadata"])
            self.assertIsNone(observations["cases"][1]["ocr"]["records"])
            self.assertIsNone(observations["cases"][1]["visual"]["labels"])
            self.assertEqual(stdout.getvalue().count('"event": "case_completed"'), 2)
            self.assertNotIn("SECRET_", partial_text)
            self.assertEqual(list(output.glob(".observations-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
