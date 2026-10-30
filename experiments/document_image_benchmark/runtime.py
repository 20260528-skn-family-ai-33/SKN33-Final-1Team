"""Offline observations only: frozen SigLIP2 and an isolated existing Paddle env.

This module never reads conditions or gold labels, installs packages, downloads
models, starts an HTTP server, or manages a process it did not create.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time

LABELS = ("person", "food_drink", "animal", "plant_flower", "vehicle",
          "building_structure", "musical_instrument", "fireworks")
SOURCE_HASHES = {
    "experiments/visual_classifier_benchmark/highres_v1/run.py":
        "fdf034601b43c5a632df25c79875e0334e870f3c9ffafd7bba63d452ac9a8d24",
    "experiments/poster_benchmark/paddle_service.py":
        "0ab420dcaa2c2e2b6029d6c8951ff519651e6bdc45fcc13100690475aea0cf89",
    "scripts/prepare_paddle.py":
        "610e48748a53de9c2c23b49f141e98b2aeb7f684f93a3906701c491817886eaf",
}
SIGLIP_SOURCE, PADDLE_SOURCE, PADDLE_VERIFY_SOURCE = SOURCE_HASHES
OFFLINE = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
           "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
           "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
MAX_PIXELS = 40_000_000
PREPROCESSING = {
    "source": "decode image; EXIF transpose; convert RGB; preserve original metadata",
    "visual": "384x384 BICUBIC aspect-preserving letterbox; RGB(128,128,128) padding; "
              "preprocessor_config mean/std; FP32; L2-normalized vision pooler; frozen linear head",
    "ocr": "full oriented RGB image decoded as BGR by existing Paddle worker; "
           "detector max-side 1280; no document orientation/unwarping/textline orientation",
    "coordinates": "pixels in the EXIF-oriented source image, before visual letterbox",
}


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _error(exc):
    return {"type": type(exc).__name__, "message": str(exc)[:2000]}


def _inside(root, relative):
    relative = Path(relative)
    if relative.is_absolute():
        raise ValueError("Image path must be relative to the packet")
    result = (Path(root) / relative).resolve()
    if not result.is_relative_to(Path(root).resolve()):
        raise ValueError("Image path escapes the packet directory")
    return result


def _module(repo, relative, name):
    path = Path(repo) / relative
    actual = sha(path)
    if actual != SOURCE_HASHES[relative]:
        raise ValueError("Audited dependency changed: " + relative)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _packages():
    names = {"torch": "torch", "transformers": "transformers", "safetensors": "safetensors",
             "numpy": "numpy", "PIL": "Pillow"}
    result = {}
    for module, distribution in names.items():
        found = importlib.util.find_spec(module) is not None
        try:
            version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            version = None
        result[module] = {"available": found, "version": version}
    return result


def _ocr_python_probe(python):
    # Introspection only: do not import Paddle or initialize its runtime.
    code = (
        "import importlib.util,importlib.metadata,json,sys; "
        "names={'paddle':'paddlepaddle','paddleocr':'paddleocr','numpy':'numpy','PIL':'Pillow'}; "
        "found={k:importlib.util.find_spec(k) is not None for k in names}; "
        "versions={d.metadata['Name']:d.version for d in importlib.metadata.distributions() "
        "if d.metadata['Name'].lower() in {'paddlepaddle','paddlepaddle-gpu','paddleocr','paddlex','numpy','pillow'}}; "
        "print(json.dumps({'python':sys.version,'executable':sys.executable,'available':found,'versions':versions}))"
    )
    process = subprocess.run([str(python), "-c", code], text=True, capture_output=True,
                             timeout=30, check=True, env={**os.environ, **OFFLINE})
    return json.loads(process.stdout)


def preflight(args):
    """Check paths, package availability and model hashes; never load a model."""
    repo = Path(args.repo).resolve()
    result = {"schema": "document-image-runtime-preflight-v1", "ready": False,
              "model_loading_executed": False, "inference_executed": False,
              "installation_executed": False, "download_executed": False,
              "cuda_verified": False, "python": sys.version, "executable": sys.executable,
              "runtime_sha256": sha(__file__), "preprocessing": PREPROCESSING,
              "sources": {}, "issues": [], "visual_ready": False, "ocr_ready": False}
    for relative, expected in SOURCE_HASHES.items():
        try:
            actual = sha(repo / relative)
            result["sources"][relative] = {"sha256": actual, "expected_sha256": expected,
                                           "matches": actual == expected}
            if actual != expected:
                raise ValueError("Audited dependency changed: " + relative)
        except Exception as exc:
            result["issues"].append({"stage": "source", "path": relative, **_error(exc)})
    try:
        result["packages"] = _packages()
        missing = [name for name, info in result["packages"].items() if not info["available"]]
        if missing:
            raise RuntimeError("Current Python lacks packages: " + ", ".join(missing))
        adapter = _module(repo, SIGLIP_SOURCE, "_document_image_siglip_preflight")
        result["visual_model"] = adapter.model_identity(Path(args.model_dir), Path(args.reference))
        result["visual_ready"] = True
    except Exception as exc:
        result["issues"].append({"stage": "visual", **_error(exc)})
    try:
        if getattr(args, "ocr_device", "cpu") != "cpu":
            raise ValueError("This observer requires OCR on CPU to preserve the existing GPU worker")
        python = Path(args.ocr_python)
        if not python.is_file() or not os.access(python, os.X_OK):
            raise FileNotFoundError("Existing Paddle Python unavailable: " + str(python))
        if sha(repo / PADDLE_SOURCE) != SOURCE_HASHES[PADDLE_SOURCE]:
            raise ValueError("Audited Paddle worker changed")
        verifier = _module(repo, PADDLE_VERIFY_SOURCE, "_document_image_paddle_verify")
        model_root = Path(args.ocr_models)
        result["ocr_models"] = {name: verifier.verify_model(model_root / model, model)
                                for name, model in (("detection", verifier.DETECTION_MODEL),
                                                    ("recognition", verifier.MODEL))}
        result["ocr_python"] = _ocr_python_probe(python)
        if not all(result["ocr_python"]["available"].values()):
            raise RuntimeError("Existing Paddle Python lacks required OCR packages")
        result["ocr_ready"] = True
    except Exception as exc:
        result["issues"].append({"stage": "ocr", **_error(exc)})
    result["ready"] = result["visual_ready"] and result["ocr_ready"] and not result["issues"]
    return result


class _PaddleChild:
    """One owned CPU subprocess; terminate only this Popen child on cleanup."""
    def __init__(self, args, directory):
        self.process = None
        self.log = None
        self.buffer = b""
        self.timeout = 240
        directory = Path(directory)
        self.log = (directory / "paddle_worker.log").open("x", encoding="utf-8")
        command = [str(args.ocr_python), str(Path(args.repo) / PADDLE_SOURCE),
                   "--device", "cpu", "--models", str(args.ocr_models)]
        env = {**os.environ, **OFFLINE, "CUDA_VISIBLE_DEVICES": "",
               "PADDLE_PDX_CACHE_HOME": str(directory / "paddlex_cache"),
               "XDG_CACHE_HOME": str(directory / "cache")}
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self.log, cwd=str(args.repo), env=env, bufsize=0)
            self.identity = self.receive()
            if self.identity.get("event") != "ready" or self.identity.get("device") != "cpu":
                raise RuntimeError("Paddle CPU worker did not report ready")
        except BaseException:
            self.close()
            raise

    def receive(self):
        deadline = time.monotonic() + self.timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                raise TimeoutError("Paddle worker timeout; inspect runtime_evidence/paddle_worker.log")
            block = os.read(self.process.stdout.fileno(), 65536)
            if not block:
                raise RuntimeError("Paddle worker exited; inspect runtime_evidence/paddle_worker.log")
            self.buffer += block
            if len(self.buffer) > 64 * 1024 * 1024:
                raise ValueError("Paddle response exceeds 64 MiB")
        line, self.buffer = self.buffer.split(b"\n", 1)
        result = json.loads(line)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("Paddle worker error: " + str(result.get("error") if isinstance(result, dict) else result))
        return result

    def predict(self, path, image_id):
        request = json.dumps({"path": str(path), "image_id": image_id}, ensure_ascii=False)
        self.process.stdin.write((request + "\n").encode("utf-8"))
        self.process.stdin.flush()
        result = self.receive()
        if result.get("image_id") != image_id:
            raise ValueError("Paddle response case ID differs")
        return result

    def close(self):
        if self.process is not None:
            if self.process.stdin and not self.process.stdin.closed:
                try:
                    self.process.stdin.close()
                except BrokenPipeError:
                    pass
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=10)
            if self.process.stdout:
                self.process.stdout.close()
        if self.log is not None:
            self.log.close()


def _records(raw, width, height):
    value = raw.get("res", raw)
    texts, scores, polygons = (value.get(key) for key in ("rec_texts", "rec_scores", "rec_polys"))
    if (any(not isinstance(items, list) for items in (texts, scores, polygons))
            or len(texts) != len(scores) or len(scores) != len(polygons) or len(texts) > 1000):
        raise ValueError("Paddle text/score/polygon count mismatch")
    records = []
    for index, (text, score, polygon) in enumerate(zip(texts, scores, polygons)):
        if not isinstance(text, str) or len(text) > 2000:
            raise ValueError("Invalid OCR text")
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Invalid OCR confidence")
        if not isinstance(polygon, list) or len(polygon) != 4:
            raise ValueError("Invalid OCR polygon")
        for point in polygon:
            if (not isinstance(point, list) or len(point) != 2 or
                    any(type(v) not in (int, float) or not math.isfinite(v) for v in point) or
                    not 0 <= point[0] <= width or not 0 <= point[1] <= height):
                raise ValueError("OCR polygon outside oriented source")
        xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
        box = [min(xs), min(ys), max(xs), max(ys)]
        area = abs(sum(polygon[i][0] * polygon[(i + 1) % 4][1] -
                       polygon[(i + 1) % 4][0] * polygon[i][1] for i in range(4)))
        if box[0] >= box[2] or box[1] >= box[3] or area == 0:
            raise ValueError("Degenerate OCR polygon")
        if text.strip():
            records.append({"id": f"paddle_{index:03d}", "text": text.strip(),
                            "confidence": float(score), "bbox_xyxy": box, "polygon": polygon,
                            "recognition_engine": "paddleocr"})
    return records


def _image(path, normalized):
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        if source.width * source.height > MAX_PIXELS:
            raise ValueError("Image exceeds the 40 megapixel observation limit")
        metadata = {"format": source.format, "original_mode": source.mode,
                    "original_dimensions": list(source.size), "exif_orientation": source.getexif().get(274, 1)}
        with ImageOps.exif_transpose(source) as oriented:
            with oriented.convert("RGB") as rgb:
                metadata.update(width=rgb.width, height=rgb.height, oriented_dimensions=list(rgb.size))
                rgb.save(normalized, format="PNG")
    return metadata


def _visual_input(path, model_dir):
    import numpy as np
    import torch
    from PIL import Image
    config = json.loads((Path(model_dir) / "preprocessor_config.json").read_text(encoding="utf-8"))
    with Image.open(path) as rgb:
        scale = min(384 / rgb.width, 384 / rgb.height)
        shape = (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale)))
        with Image.new("RGB", (384, 384), (128, 128, 128)) as canvas:
            with rgb.resize(shape, Image.Resampling.BICUBIC) as resized:
                canvas.paste(resized, ((384 - shape[0]) // 2, (384 - shape[1]) // 2))
            pixels = np.asarray(canvas, dtype=np.float32).copy() / 255.0
    mean = torch.tensor(config["image_mean"], dtype=torch.float32).view(1, 3, 1, 1)
    std = torch.tensor(config["image_std"], dtype=torch.float32).view(1, 3, 1, 1)
    if not bool(torch.isfinite(mean).all() and torch.isfinite(std).all() and (std > 0).all()):
        raise ValueError("Invalid frozen model normalization")
    x = torch.from_numpy(pixels).permute(2, 0, 1).unsqueeze(0).contiguous()
    return ((x - mean) / std).contiguous().to("cuda:0")


def _failed(stage, exc):
    return {"status": "error", "records" if stage == "ocr" else "labels": None, "error": _error(exc)}


def _progress(event, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, allow_nan=False), flush=True)


def _checkpoint(result, destination):
    """Replace the observation-only checkpoint atomically within its output dir."""
    destination = Path(destination)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=".observations-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _case_complete(result, observation, destination, started):
    runtime = result["runtime"]
    runtime["observed_case_count"] += 1
    runtime["wall_seconds"] = time.perf_counter() - started
    _checkpoint(result, destination)
    _progress("case_completed", case_id=observation["case_id"],
              completed=runtime["observed_case_count"], ocr_status=observation["ocr"]["status"],
              visual_status=observation["visual"]["status"])


def observe(args, packet, packet_root, output_dir):
    """Observe image bytes only; errors are explicit null observations per case."""
    if packet.get("schema") != "document-image-review-v1" or not isinstance(packet.get("cases"), list):
        raise ValueError("Expected document-image-review-v1 packet")
    packet_root, output_dir = Path(packet_root).resolve(), Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence = output_dir / "runtime_evidence"
    evidence.mkdir(exist_ok=False)
    os.environ.update(OFFLINE)
    _progress("runtime_preflight_started", cases=len(packet["cases"]))
    check = preflight(args)
    runtime = {"preflight": check, "preprocessing": PREPROCESSING, "engine": "siglip_ocr",
               "status": "running",
               "model_loading_executed": False, "inference_executed": False,
               "installation_executed": False, "download_executed": False,
               "ocr_device": "cpu", "visual_device": "cuda:0", "visual_precision": "fp32",
               "observed_case_count": 0, "ocr_attempts": 0, "visual_attempts": 0,
               "startup_errors": {}, "worker_cleanup": "not_started"}
    result = {"schema": "document-image-observations-v1", "packet_sha256": sha(packet_root / "packet.json"),
              "engine": "siglip_ocr", "cases": [], "runtime": runtime}
    partial = output_dir / "observations.partial.json"
    _checkpoint(result, partial)
    _progress("runtime_preflight_completed", visual_ready=check["visual_ready"], ocr_ready=check["ocr_ready"])
    worker = None
    backbone = head = adapter = torch = None
    ocr_error = RuntimeError("OCR preflight failed; inspect runtime.preflight.issues")
    visual_error = RuntimeError("Visual preflight failed; inspect runtime.preflight.issues")
    started = time.perf_counter()
    try:
        if check["ocr_ready"]:
            try:
                _progress("model_loading_started", engine="paddleocr", device="cpu")
                runtime["model_loading_executed"] = True
                worker = _PaddleChild(args, evidence)
                runtime["ocr_worker"] = worker.identity
                runtime["worker_cleanup"] = "pending"
                _progress("model_loading_completed", engine="paddleocr")
            except Exception as exc:
                ocr_error = exc
                runtime["startup_errors"]["ocr"] = _error(exc)
                _progress("model_loading_failed", engine="paddleocr", error=_error(exc))
        if check["visual_ready"]:
            try:
                _progress("model_loading_started", engine="siglip2", device="cuda:0")
                import torch
                if not torch.cuda.is_available():
                    raise RuntimeError("CUDA unavailable; no CPU fallback or installation attempted")
                runtime["cuda"] = {"device_name": torch.cuda.get_device_name(0),
                                   "free_bytes_before_load": torch.cuda.mem_get_info(0)[0]}
                adapter = _module(args.repo, SIGLIP_SOURCE, "_document_image_siglip_runtime")
                runtime["model_loading_executed"] = True
                backbone, head = adapter.load_models(args, check["visual_model"]["checkpoint"], device="cuda:0")
                backbone.eval().requires_grad_(False)
                head.eval().requires_grad_(False)
                _progress("model_loading_completed", engine="siglip2")
            except Exception as exc:
                visual_error = exc
                runtime["startup_errors"]["visual"] = _error(exc)
                backbone = head = None
                _progress("model_loading_failed", engine="siglip2", error=_error(exc))
        for index, case in enumerate(packet["cases"]):
            case_id = case["id"]
            _progress("case_started", case_id=case_id, index=index + 1, total=len(packet["cases"]))
            observation = {"case_id": case_id, "image_sha256": None, "metadata": None,
                           "ocr": _failed("ocr", ocr_error), "visual": _failed("visual", visual_error)}
            result["cases"].append(observation)
            normalized = evidence / f"image_{index:05d}.png"
            try:
                image = _inside(packet_root, case["image"]["path"])
                observation["image_sha256"] = sha(image)
                if observation["image_sha256"] != case["image"]["sha256"]:
                    raise ValueError("Input image hash differs from packet")
                metadata = observation["metadata"] = _image(image, normalized)
                observation["normalized_image_sha256"] = sha(normalized)
            except Exception as exc:
                observation.update(ocr=_failed("ocr", exc), visual=_failed("visual", exc))
                _case_complete(result, observation, partial, started)
                continue
            if worker is not None:
                try:
                    runtime["inference_executed"] = True
                    runtime["ocr_attempts"] += 1
                    response = worker.predict(normalized, case_id)
                    if response.get("input_dimensions") != [metadata["width"], metadata["height"]]:
                        raise ValueError("OCR image dimensions differ from oriented source")
                    raw_path = evidence / f"ocr_{index:05d}.json"
                    with raw_path.open("x", encoding="utf-8") as stream:
                        json.dump(response, stream, ensure_ascii=False, indent=2, allow_nan=False)
                    records = _records(response["raw"], metadata["width"], metadata["height"])
                    observation["ocr"] = {"status": "success", "records": records, "error": None,
                                          "engine_ms": response.get("engine_ms"),
                                          "raw_sha256": sha(raw_path)}
                except Exception as exc:
                    observation["ocr"] = _failed("ocr", exc)
                    # A failed worker must not feed a stale response to the next case.
                    worker.close()
                    worker = None
                    ocr_error = RuntimeError("OCR worker stopped after case error: " + str(exc))
                    runtime["worker_cleanup"] = "owned_child_stopped_after_error"
            if backbone is not None and head is not None:
                try:
                    x = _visual_input(normalized, args.model_dir)
                    runtime["inference_executed"] = True
                    runtime["visual_attempts"] += 1
                    torch.cuda.synchronize()
                    begin = time.perf_counter()
                    with torch.inference_mode():
                        values = torch.sigmoid(adapter.logits(backbone, head, x, 384))[0].cpu().tolist()
                    torch.cuda.synchronize()
                    if len(values) != len(LABELS) or any(not math.isfinite(v) or not 0 <= v <= 1 for v in values):
                        raise ValueError("Invalid SigLIP probabilities")
                    thresholds = check["visual_model"]["thresholds"]
                    observation["visual"] = {"status": "success", "error": None,
                        "labels": {label: {"score": value, "threshold": threshold,
                                           "prediction": int(value >= threshold)}
                                   for label, value, threshold in zip(LABELS, values, thresholds)},
                        "model_ms": (time.perf_counter() - begin) * 1000}
                    del x
                except Exception as exc:
                    observation["visual"] = _failed("visual", exc)
            _case_complete(result, observation, partial, started)
        runtime["status"] = "completed"
    except BaseException as exc:
        runtime["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "error"
        runtime["fatal_error"] = _error(exc)
        raise
    finally:
        if worker is not None:
            worker.close()
            runtime["worker_cleanup"] = "owned_child_stopped"
        # These references belong to this call. Never touch another worker/PID.
        backbone = head = None
        runtime["wall_seconds"] = time.perf_counter() - started
        _checkpoint(result, partial)
        _progress("observation_finished", status=runtime["status"],
                  completed=runtime["observed_case_count"], total=len(packet["cases"]))
    return result
