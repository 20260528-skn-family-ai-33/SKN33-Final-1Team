"""Offline contract tests. No models, API keys, GPU, or paid endpoint required."""
import base64
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from backend.app import ocr_runtime as runtime
from backend.app import runpod_worker


class OCRRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.image = self.root / "test.png"
        Image.new("RGB", (12, 9), (255, 0, 0)).save(self.image)
        self.env = patch.dict(os.environ, {"RUN_TARGET": "local", "OCR_MODEL_DIR": str(self.root / "models"),
                                           "OCR_ALLOW_DOWNLOAD": "0", "RUNPOD_API_KEY": "test-secret",
                                           "RUNPOD_ENDPOINT_ID": "test-endpoint", "RUNPOD_POLL_SECONDS": "0.01"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        runtime._READERS.clear()
        runtime._DEVICE_FAILURES.clear()
        runtime._ACTIVE_DEVICE.clear()

    def result(self, device="cuda"):
        return {"engine": "easyocr", "status": "success", "device": device,
                "input_dimensions": [12, 9], "records": [{"polygon": [[1, 1], [8, 1], [8, 6], [1, 6]],
                                                          "text": "행사", "confidence": .9}], "attempts": []}

    def test_device_priority(self):
        for cuda, mps, expected in [(True, True, "cuda"), (False, True, "mps"), (False, False, "cpu")]:
            torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda),
                                    backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)))
            with patch.object(runtime.importlib, "import_module", return_value=torch):
                available = runtime._device_availability()
                self.assertEqual(next(d for d, ok in available if ok), expected)

    def test_backend_oom_falls_through_and_is_recorded(self):
        loader = Mock(side_effect=[RuntimeError("CUDA out of memory"), RuntimeError("MPS backend out of memory"), object()])
        with patch.object(runtime, "_device_availability", return_value=[("cuda", True), ("mps", True), ("cpu", True)]), \
             patch.object(runtime, "_load_reader", loader), patch.object(runtime, "_release_device"):
            value, device, attempts = runtime._local_operation(lambda reader: "done")
        self.assertEqual((value, device), ("done", "cpu"))
        self.assertEqual([a["status"] for a in attempts], ["fallback", "fallback", "success"])
        self.assertEqual([call.args[0] for call in loader.call_args_list], ["cuda", "mps", "cpu"])

    def test_inference_backend_failure_falls_back(self):
        gpu = Mock()
        gpu.readtext.side_effect = NotImplementedError("operation not implemented for MPS")
        cpu = Mock()
        cpu.readtext.return_value = [([[1, 1], [8, 1], [8, 6], [1, 6]], "행사", .8)]
        with patch.object(runtime, "_device_availability", return_value=[("cuda", False), ("mps", True), ("cpu", True)]), \
             patch.object(runtime, "_load_reader", side_effect=[gpu, cpu]), patch.object(runtime, "_release_device"):
            result = runtime.recognize_image(self.image)
        self.assertEqual(result["device"], "cpu")
        self.assertEqual(result["records"][0]["bbox_xyxy"], [1, 1, 8, 6])
        self.assertEqual(result["attempts"][1]["status"], "fallback")
        self.assertEqual(cpu.readtext.call_args.args[0][0, 0].tolist(), [0, 0, 255])

    def test_download_or_model_errors_never_fallback(self):
        for error in [FileNotFoundError("Missing model downloads disabled"), RuntimeError("MPS download checksum mismatch"),
                      ValueError("invalid image"), RuntimeError("unrelated model failure")]:
            with self.subTest(error=error), patch.object(runtime, "_device_availability", return_value=[("mps", True), ("cpu", True)]), \
                 patch.object(runtime, "_load_reader", side_effect=error) as loader:
                with self.assertRaises(runtime.OCRRuntimeError):
                    runtime._local_operation(lambda reader: None)
                self.assertEqual(loader.call_count, 1)

    def test_reader_loaded_once_for_concurrent_warmup(self):
        easyocr = SimpleNamespace(Reader=Mock(return_value=object()))
        with patch.object(runtime, "_device_availability", return_value=[("cpu", True)]), \
             patch.object(runtime.importlib, "import_module", return_value=easyocr):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: runtime.warmup_models(), range(8)))
        self.assertEqual(easyocr.Reader.call_count, 1)
        self.assertIs(easyocr.Reader.call_args.kwargs["download_enabled"], False)
        self.assertIs(easyocr.Reader.call_args.kwargs["gpu"], False)

    def test_health_does_not_download_load_or_make_http_calls(self):
        with patch.object(runtime.importlib.util, "find_spec", return_value=True), \
             patch.object(runtime, "_device_availability", return_value=[("cpu", True)]), \
             patch.object(runtime, "_load_reader") as load, patch.object(runtime, "_http_json") as http:
            status = runtime.get_runtime_status()
        self.assertFalse(status["ready"])
        self.assertEqual(status["device"], "cpu")
        load.assert_not_called()
        http.assert_not_called()
        self.assertFalse((self.root / "models").exists())

    def test_local_is_default_and_never_calls_remote(self):
        os.environ.pop("RUN_TARGET")
        with patch.object(runtime, "_recognize_local", return_value={"status": "success"}) as local, \
             patch.object(runtime, "_recognize_runpod") as remote:
            runtime.recognize_image(self.image)
        local.assert_called_once()
        remote.assert_not_called()

    def test_runpod_async_contract_and_no_credentials_in_result(self):
        os.environ["RUN_TARGET"] = "runpod"
        with patch.object(runtime, "_http_json", side_effect=[{"id": "job-123", "status": "IN_QUEUE"},
                                                             {"id": "job-123", "status": "COMPLETED", "output": self.result()}]) as http, \
             patch.object(runtime.time, "sleep"):
            result = runtime.recognize_image(self.image)
        self.assertEqual(result["execution_target"], "runpod")
        self.assertEqual(result["device"], "cuda")
        self.assertEqual(result["job_id"], "job-123")
        self.assertEqual(http.call_args_list[0].args[0], "POST")
        payload = http.call_args_list[0].args[4]
        self.assertEqual(base64.b64decode(payload["input"]["image_base64"]), self.image.read_bytes())
        self.assertEqual(http.call_args_list[1].args[0], "GET")
        self.assertNotIn("test-secret", repr(result))

    def test_runpod_ambiguous_submit_does_not_retry(self):
        os.environ["RUN_TARGET"] = "runpod"
        with patch.object(runtime, "_http_json", side_effect=runtime.OCRRuntimeError("network request failed")) as http:
            with self.assertRaises(runtime.OCRRuntimeError):
                runtime.recognize_image(self.image)
        self.assertEqual(http.call_count, 1)

    def test_runpod_status_retry_is_bounded_and_idempotent(self):
        os.environ["RUN_TARGET"] = "runpod"
        with patch.object(runtime, "_http_json", side_effect=[{"id": "job-1", "status": "IN_QUEUE"},
            runtime._RemoteHTTPError(429), {"status": "COMPLETED", "output": self.result()}]) as http, patch.object(runtime.time, "sleep"):
            runtime.recognize_image(self.image)
        self.assertEqual([c.args[0] for c in http.call_args_list], ["POST", "GET", "GET"])

    def test_runpod_auth_error_is_not_retried_and_job_is_cancelled(self):
        os.environ["RUN_TARGET"] = "runpod"
        with patch.object(runtime, "_http_json", side_effect=[{"id": "job-1", "status": "IN_QUEUE"},
                                                              runtime._RemoteHTTPError(401), {}]) as http, patch.object(runtime.time, "sleep"):
            with self.assertRaises(runtime.OCRRuntimeError):
                runtime.recognize_image(self.image)
        self.assertEqual([c.args[0] for c in http.call_args_list], ["POST", "GET", "POST"])
        self.assertTrue(http.call_args_list[-1].args[1].endswith("/cancel/job-1"))

    def test_runpod_timeout_cancels_known_job(self):
        os.environ.update(RUN_TARGET="runpod", RUNPOD_TIMEOUT_SECONDS="1")
        with patch.object(runtime, "_http_json", side_effect=[{"id": "job-1", "status": "IN_QUEUE"}, {}]) as http, \
             patch.object(runtime.time, "monotonic", side_effect=[0, 2]):
            with self.assertRaisesRegex(runtime.OCRRuntimeError, "timeout"):
                runtime.recognize_image(self.image)
        self.assertTrue(http.call_args_list[-1].args[1].endswith("/cancel/job-1"))

    def test_remote_output_dimensions_must_match(self):
        with self.assertRaisesRegex(runtime.OCRRuntimeError, "dimensions"):
            runtime._validate_remote_result(self.result(), (100, 200))

    def test_invalid_polygon_is_rejected_before_clipping(self):
        for invalid in [float("nan"), float("inf"), -float("inf")]:
            with self.subTest(invalid=invalid), self.assertRaisesRegex(runtime.OCRRuntimeError, "polygon"):
                runtime._records([([[invalid, 0]] * 4, "bad", .5)], 12, 9)

    def test_polygon_clipped_to_zero_area_is_rejected(self):
        with self.assertRaisesRegex(runtime.OCRRuntimeError, "degenerate"):
            runtime._records([([[20, 0], [40, 0], [40, 9], [20, 9]], "bad", .5)], 12, 9)

    def test_missing_torchvision_means_not_ready(self):
        with patch.object(runtime.importlib.util, "find_spec", side_effect=lambda name: name != "torchvision" or None):
            status = runtime.get_runtime_status()
        self.assertFalse(status["ready"])
        self.assertIn("torchvision", status["detail"])

    def test_processed_local_image_can_exceed_upload_byte_limit(self):
        # Mock file metadata to exercise byte policy without allocating 21 MiB.
        info = SimpleNamespace(st_mode=stat.S_IFREG, st_size=21 * 1024 * 1024)
        with patch.object(Path, "stat", return_value=info):
            image = runtime._read_image(self.image)
        self.assertEqual(image.size, (12, 9))

    def test_processed_local_image_still_has_128mib_limit(self):
        info = SimpleNamespace(st_mode=stat.S_IFREG, st_size=128 * 1024 * 1024 + 1)
        with patch.object(Path, "stat", return_value=info), self.assertRaisesRegex(runtime.OCRRuntimeError, "128 MiB"):
            runtime._read_image(self.image)

    def test_processed_image_still_has_40mp_limit(self):
        with patch("PIL.Image.open") as decoded:
            decoded.return_value.__enter__.return_value = SimpleNamespace(width=10000, height=4001)
            with self.assertRaisesRegex(runtime.OCRRuntimeError, "40 megapixel"):
                runtime._read_image(self.image)

    def test_remote_size_limit_has_safe_code_and_makes_no_request(self):
        os.environ["RUN_TARGET"] = "runpod"
        info = SimpleNamespace(st_mode=stat.S_IFREG, st_size=8 * 1024 * 1024)
        with patch.object(Path, "stat", return_value=info), patch.object(runtime, "_http_json") as http:
            with self.assertRaises(runtime.OCRRuntimeError) as caught:
                runtime.recognize_image(self.image)
        self.assertEqual(caught.exception.code, "remote_image_too_large")
        http.assert_not_called()

    def test_worker_validates_input_and_uses_local_even_with_remote_target(self):
        os.environ["RUN_TARGET"] = "runpod"
        for value in [None, "", "not base64!"]:
            with self.assertRaises(ValueError):
                runpod_worker.handler({"input": {"image_base64": value}})
        with patch.object(runpod_worker, "_recognize_local", return_value=self.result()) as local:
            runpod_worker.handler({"input": {"image_base64": base64.b64encode(self.image.read_bytes()).decode()}})
        local.assert_called_once()
        self.assertFalse(local.call_args.args[0].exists())


if __name__ == "__main__":
    unittest.main()
