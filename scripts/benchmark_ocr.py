"""여러 포스터의 기존 OCR을 같은 입력으로 비교하고 실행별 원시 근거를 보존한다.

python -m scripts.benchmark_ocr --manifest data/benchmarks/posters-v1.json
비교용 기본 split은 tuning이다. holdout 실행은 --split holdout으로 명시한다.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from uuid import uuid4

from PIL import Image, ImageDraw, ImageOps

from scripts.ocr_metrics import evaluate, validate_label, valid_box
from scripts.paths import PROJECT_ROOT, artifact_path, output_directory, resolve_source

VARIANTS = ("rgb_2x", "rgb_1x", "gray_2x")
PIPELINES = ("baseline", "multiline", "adaptive", "paddle", "paddle-only", "qwen")
DEFAULT_MANIFEST = PROJECT_ROOT / "data/benchmarks/posters-v1.json"


def write_json(path, value):
    """중간 실패에도 이전 결과가 손상되지 않게 JSON을 원자적으로 저장한다."""
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def sha256(path):
    """모델을 메모리에 통째로 올리지 않고 파일 식별 해시를 계산한다."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_image(source):
    """웹 업로드와 같은 EXIF·흰 배경 정규화 및 입력 제한을 적용한다."""
    from backend.app.api import _normalise_image, MAX_UPLOAD_BYTES
    if not 0 < source.stat().st_size <= MAX_UPLOAD_BYTES:
        raise ValueError("Source exceeds the web upload byte limit")
    from fastapi import HTTPException
    try:
        return _normalise_image(source.read_bytes())
    except HTTPException as exc:
        raise ValueError(exc.detail) from None


def prepare_variant(rgb, name):
    """정규화 원본에서 지정한 전처리만 적용하고 좌표 변환 배율을 반환한다."""
    if name not in VARIANTS:
        raise ValueError("Unknown preprocessing variant")
    scale = 1 if name == "rgb_1x" else 2
    image = rgb.copy() if scale == 1 else rgb.resize((rgb.width * 2, rgb.height * 2), Image.Resampling.LANCZOS)
    if name == "gray_2x":
        image = ImageOps.grayscale(image).convert("RGB")
    return image, {"coordinate_space": "normalized_original_pixels", "scale": scale,
                   "input_to_normalized_matrix": [[1 / scale, 0, 0], [0, 1 / scale, 0], [0, 0, 1]]}


def map_records(raw, dimensions, scale):
    """입력 박스·다각형을 검증한 뒤 정규화 원본 좌표로 복원한다."""
    input_size = [value * scale for value in dimensions]
    if raw.get("status") != "success" or raw.get("input_dimensions") != input_size:
        raise ValueError("OCR status/dimensions mismatch")
    if raw.get("engine") not in {"easyocr", "paddleocr", "easyocr+paddleocr", "easyocr+qwen3-vl"} or raw.get("execution_target") != "local" or raw.get("device") not in {"cpu", "cuda", "mps", "gpu", "unknown"}:
        raise ValueError("Unexpected baseline engine/target/device")
    records = raw.get("records")
    if not isinstance(records, list) or len(records) > 10_000:
        raise ValueError("Invalid OCR records")
    mapped, ids = [], set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Invalid OCR record")
        # 엔진 출처가 유실되어 EasyOCR의 낮은 임계값으로 채점되지 않게 한다.
        engine = record.get("recognition_engine", "paddleocr" if raw["engine"] == "paddleocr" else "easyocr")
        if not isinstance(engine, str) or engine not in {"easyocr", "paddleocr", "qwen3-vl"}:
            raise ValueError("Invalid recognition engine")
        if raw["engine"] == "paddleocr" and engine != "paddleocr":
            raise ValueError("Paddle payload recognition engine mismatch")
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id or record_id in ids or not isinstance(record.get("text"), str):
            raise ValueError("Invalid OCR record ID/text")
        ids.add(record_id)
        if not valid_box(record.get("bbox_xyxy"), input_size):
            raise ValueError("Invalid OCR box")
        confidence = record.get("confidence")
        if confidence is not None and (type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError("Invalid OCR confidence")
        polygon = record.get("polygon")
        if not isinstance(polygon, list) or len(polygon) < 4 or any(
            not isinstance(p, list) or len(p) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= limit
                                                        for v, limit in zip(p, input_size)) for p in polygon
        ):
            raise ValueError("Invalid OCR polygon")
        restored = {**record, "input_bbox_xyxy": record["bbox_xyxy"], "input_polygon": polygon,
                    "bbox_xyxy": [v / scale for v in record["bbox_xyxy"]],
                    "polygon": [[x / scale, y / scale] for x, y in polygon]}
        if raw["engine"] == "paddleocr":
            restored["recognition_engine"] = engine
        if record.get("alternatives"):
            if any(not valid_box(a.get("bbox_xyxy"), input_size) for a in record["alternatives"]):
                raise ValueError("Invalid alternative box")
            restored["alternatives"] = [{**a, "bbox_xyxy": [v / scale for v in a["bbox_xyxy"]]} for a in record["alternatives"]]
        mapped.append(restored)
    return mapped


def load_cases(manifest_path, project):
    """데이터 분할을 고정하고 이미지 중복·변경과 잘못된 라벨을 검사한다."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "poster.benchmark.v1" or not manifest.get("cases"):
        raise ValueError("Invalid benchmark manifest")
    cases, ids, hashes, pixel_hashes = [], set(), set(), set()
    for case in manifest["cases"]:
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id.isascii() or not case_id.replace("-", "").isalnum() or case_id in ids:
            raise ValueError("Case IDs must be unique ASCII letters/numbers/hyphens")
        if case["split"] not in {"tuning", "holdout"}:
            raise ValueError("Unknown benchmark split")
        source = resolve_source({"path": case["image"], "path_base": "project_root"}, project)
        digest = sha256(source)
        rgb, metadata = normalize_image(source)
        pixel_digest = hashlib.sha256(str(rgb.size).encode() + rgb.tobytes()).hexdigest()
        if digest != case["sha256"] or digest in hashes or pixel_digest in pixel_hashes:
            raise ValueError("Source hash mismatch or duplicate image across cases/splits")
        label = None
        if case.get("label"):
            label_path = resolve_source({"path": case["label"], "path_base": "project_root"}, project)
            label = json.loads(label_path.read_text(encoding="utf-8"))
            validate_label(label, digest, rgb.size)
        cases.append({"case": case, "source": source, "label": label, "metadata": metadata})
        ids.add(case_id)
        hashes.add(digest)
        pixel_hashes.add(pixel_digest)
    return manifest, cases


def environment_metadata(pipeline="baseline"):
    """실험 코드·패키지·모델 해시를 기록하되 환경 변수와 비밀값은 수집하지 않는다."""
    from backend.app.ocr_runtime import MODEL_FILES, _model_dir
    packages = {}
    for name in ("easyocr", "torch", "torchvision", "numpy", "Pillow", "opencv-python-headless"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    models = [{"name": name, "sha256": sha256(_model_dir() / name) if (_model_dir() / name).is_file() else None} for name in MODEL_FILES]
    code = ["scripts/benchmark_ocr.py", "scripts/ocr_metrics.py", "backend/app/api.py", "backend/app/ocr_runtime.py",
            "backend/app/extract_event.py", "backend/app/review_rules.py", "backend/app/event_layout.py", "backend/app/adaptive_ocr.py",
            "backend/app/model_ocr.py", "backend/app/paddle_worker.py", "backend/app/paddle_ocr.py",
            "backend/app/paddle_full_worker.py", "scripts/prepare_paddle.py"]
    # 단독 경로에는 EasyOCR 설정을 실행 설정으로 표시하지 않는다.
    settings = ({"detection_model": "PP-OCRv5_server_det", "recognition_model": "korean_PP-OCRv5_mobile_rec",
                 "device": "cpu", "download_enabled": False, "doc_preprocessing": False}
                if pipeline == "paddle-only" else
                {"languages": ["ko", "en"], "decoder": "greedy (existing default)",
                 "paragraph": False, "batch_size": 1, "workers": 0, "download_enabled": False})
    return {"python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine(),
            "packages": packages, "models": models, "code_sha256": {name: sha256(PROJECT_ROOT / name) for name in code},
            "ocr_settings": settings}


def peak_rss_bytes():
    """지원되는 OS의 프로세스 최대 RSS를 기록한다. GPU 전체 메모리 값은 아니다."""
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform == "darwin" else value * 1024)
    except ImportError:
        return None


def worker(input_path, output_path, pipeline="baseline"):
    """라벨 없는 별도 프로세스에서 기존 OCR을 한 번 실행하고 시간·실패를 기록한다."""
    # 외부 RUN_TARGET/OCR_ALLOW_DOWNLOAD가 설정되어 있어도 유료 전환·다운로드하지 않는다.
    os.environ["RUN_TARGET"] = "local"
    os.environ["OCR_ALLOW_DOWNLOAD"] = "0"
    started = time.perf_counter()
    result = {"status": "failed"}
    try:
        if pipeline == "paddle-only":
            # 탐지부터 인식까지 Paddle 작업자만 실행하며 EasyOCR 기준선을 호출하지 않는다.
            from backend.app.paddle_ocr import recognize_paddle
            raw = recognize_paddle(input_path, output_path.parent / pipeline)
            result.update(status="success", raw=raw, **raw.get("profile", {}))
            write_json(output_path, result)
            return 0
        if pipeline in {"paddle", "qwen"}:
            from backend.app.model_ocr import recognize_models
            raw = recognize_models(input_path, output_path.parent / pipeline, pipeline)
            result.update(status="success", raw=raw, refinement_status=raw["refinement_status"], **raw.get("profile", {}))
            write_json(output_path, result)
            return 0
        if pipeline == "adaptive":
            from backend.app.adaptive_ocr import recognize_adaptive
            raw = recognize_adaptive(input_path, output_path.parent / "adaptive")
            result.update(status="success", raw=raw, **raw.get("profile", {}))
            result["worker_seconds"] = time.perf_counter() - started
            write_json(output_path, result)
            return 0
        from backend.app.ocr_runtime import warmup_models, recognize_image
        warmup = warmup_models(allow_download=False)
        import torch
        device = warmup["device"]
        if device in {"mps", "cuda"}:
            getattr(torch, device).synchronize()
        result["load_seconds"] = time.perf_counter() - started
        inference_start = time.perf_counter()
        raw = recognize_image(input_path)
        if raw["device"] in {"mps", "cuda"}:
            getattr(torch, raw["device"]).synchronize()
        result.update(status="success", raw=raw, warmup=warmup, inference_seconds=time.perf_counter() - inference_start)
    except Exception as exc:
        result.update(error_type=type(exc).__name__, error=str(exc), attempts=getattr(exc, "attempts", []))
    result.update(worker_seconds=time.perf_counter() - started, peak_rss_bytes=peak_rss_bytes())
    write_json(output_path, result)
    return 0 if result["status"] == "success" else 1


def run_worker(input_path, directory, timeout, pipeline="baseline"):
    """시간 초과 시 자식 프로세스를 종료하고 다음 이미지의 측정을 계속한다."""
    if pipeline == "paddle-only":
        # 단독 어댑터가 실제 Paddle 자식 프로세스를 직접 종료할 수 있게 한다.
        from backend.app.paddle_ocr import recognize_paddle
        started = time.perf_counter()
        try:
            raw = recognize_paddle(input_path, directory / pipeline, timeout=timeout)
            result = {"status": "success", "raw": raw, **raw.get("profile", {})}
        except Exception as exc:
            result = {"status": "failed", "error_type": type(exc).__name__}
        result["wall_seconds"] = time.perf_counter() - started
        write_json(directory / "worker.json", result)
        return result
    if pipeline in {"paddle", "qwen"}:
        # 어댑터가 개별 작업자·HTTP 요청을 제한하므로 바깥 프로세스를 중첩하지 않는다.
        from backend.app.model_ocr import recognize_models, MAX_SECONDS
        started = time.perf_counter()
        try:
            raw = recognize_models(input_path, directory / pipeline, pipeline, timeout=min(timeout, MAX_SECONDS))
            result = {"status": "success", "raw": raw, "refinement_status": raw["refinement_status"], **raw.get("profile", {})}
        except Exception as exc:
            result = {"status": "failed", "error_type": type(exc).__name__}
        result["wall_seconds"] = time.perf_counter() - started
        write_json(directory / "worker.json", result)
        return result
    if pipeline == "adaptive":
        # 중첩 프로세스를 만들지 않아 비교 제한시간이 끝나면 실제 추론도 종료된다.
        from backend.app.adaptive_ocr import recognize_adaptive, HARD_TIMEOUT_SECONDS
        started = time.perf_counter()
        try:
            raw = recognize_adaptive(input_path, directory / "adaptive", timeout=min(timeout, HARD_TIMEOUT_SECONDS))
            result = {"status": "success", "raw": raw, "refinement_status": raw["refinement_status"], **raw.get("profile", {})}
        except Exception as exc:
            result = {"status": "failed", "error_type": type(exc).__name__}
        result["wall_seconds"] = time.perf_counter() - started
        write_json(directory / "worker.json", result)
        return result
    output = directory / "worker.json"
    env = {**os.environ, "RUN_TARGET": "local", "OCR_ALLOW_DOWNLOAD": "0", "PYTHONDONTWRITEBYTECODE": "1"}
    started = time.perf_counter()
    with (directory / "worker.log").open("w", encoding="utf-8") as log:
        try:
            completed = subprocess.run([sys.executable, "-m", "scripts.benchmark_ocr", "--worker-input", str(input_path),
                                        "--worker-output", str(output), "--pipeline", pipeline], cwd=PROJECT_ROOT, env=env,
                                       stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False)
            result = json.loads(output.read_text(encoding="utf-8")) if output.is_file() else {"status": "failed", "error_type": "WorkerExit"}
            if not isinstance(result, dict) or result.get("status") not in {"success", "failed"}:
                raise ValueError("Malformed worker result")
            if completed.returncode != 0:
                result.update(status="failed", returncode=completed.returncode)
        except subprocess.TimeoutExpired:
            result = {"status": "failed", "error_type": "Timeout", "timeout_seconds": timeout}
        except (ValueError, OSError) as exc:
            result = {"status": "failed", "error_type": type(exc).__name__}
    result["wall_seconds"] = time.perf_counter() - started
    return result


def summarize(rows):
    """실패 횟수를 남기고 승인된 라벨과 초안의 지표를 따로 집계한다."""
    groups = []
    for variant in dict.fromkeys(r["variant"] for r in rows):
        selected = [r for r in rows if r["variant"] == variant]
        successes = [r for r in selected if r["status"] == "success"]
        group = {"variant": variant, "runs": len(selected), "succeeded": len(successes), "failed": len(selected) - len(successes)}
        for key in ("inference_seconds", "load_seconds", "wall_seconds", "peak_rss_bytes"):
            values = [r[key] for r in successes if r.get(key) is not None]
            group[f"median_{key}"] = statistics.median(values) if values else None
        group["devices"] = sorted({r["device"] for r in successes})
        group["metrics"] = {}
        for status in ("draft_diagnostic", "human_approved"):
            evaluations = [r["evaluation"] for r in successes if r.get("evaluation", {}).get("reference_status") == status]
            counts = {key: sum(e["counts"][key] for e in evaluations) for key in evaluations[0]["counts"]} if evaluations else {}
            group["metrics"][status] = {"evaluated_runs": len(evaluations), **counts,
                                        "core_cer": counts.get("edits", 0) / counts["characters"] if counts.get("characters") else None}
        groups.append(group)
    return groups


def markdown_report(report):
    """측정 분모와 한계를 명시한 비교 보고서를 생성한다."""
    lines = ["# OCR 처리 경로별 성능 기록", "", f"- 실행 ID: `{report['run_id']}`", f"- 입력 분할: `{report['split']}`",
             f"- 처리 경로: `{report.get('pipeline', 'baseline')}`",
             "- 원시 결과와 라벨 스냅샷은 이 실행 폴더에 보존됩니다.",
             "- 초안 일치율은 정확도 확정값이 아닙니다. 사람 승인 지표와 합산하지 않습니다.",
             "- 시간은 이미지당 새 프로세스에서 모델을 로드한 뒤 첫 추론을 측정합니다. 상시 서버의 평균 지연을 뜻하지 않습니다.",
             "- RSS는 프로세스 최대 상주 메모리이며 전체 GPU 사용량이 아닙니다. 지원하지 않는 OS/실패 실행은 null입니다.",
             "- CER는 라벨 영역 내 OCR 중심점으로 대조한 문자 오류율입니다. NFC/공백만 정규화하며 문장부호는 유지합니다.",
             "- 실패·미라벨 입력의 품질 점수는 제외하고 실행 실패/평가 수를 별도로 표시합니다.", "",
             "| 입력 | 성공/실행 | 추론 중앙값(s) | 로드 중앙값(s) | 최대 RSS 중앙값(MiB) |",
             "|---|---:|---:|---:|---:|"]
    for group in report["summary"]:
        values = [group.get("median_inference_seconds"), group.get("median_load_seconds"),
                  group["median_peak_rss_bytes"] / 1048576 if group.get("median_peak_rss_bytes") else None]
        cells = [f"{value:.3f}" if value is not None else "—" for value in values]
        lines.append(f"| {group['variant']} | {group['succeeded']}/{group['runs']} | " + " | ".join(cells) + " |")
    lines += ["", "| 입력 | 라벨 상태 | 평가 실행 | 핵심 CER | 필드 일치/가시 필드 | 누락 | 오통과/위반 기준 | 확인 필요/전체 |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for group in report["summary"]:
        for status, m in group["metrics"].items():
            if not m["evaluated_runs"]:
                continue
            cer = f"{m['core_cer']:.1%}" if m["core_cer"] is not None else "—"
            lines.append(f"| {group['variant']} | {status} | {m['evaluated_runs']} | {cer} | {m['exact_fields']}/{m['visible_fields']} | {m['missing_fields']} | {m['false_pass']}/{m['expected_fail']} | {m['review']}/{m['criteria']} |")
    return "\n".join(lines) + "\n"


def run_benchmark(manifest_path, output, *, project=PROJECT_ROOT, split="tuning", variants=VARIANTS, repeats=1, timeout=180, pipeline="baseline"):
    """실행별 새 폴더를 만들고 같은 이미지·라벨·설정으로 비교한다."""
    if split not in {"tuning", "holdout"} or not variants or len(set(variants)) != len(variants) or any(v not in VARIANTS for v in variants):
        raise ValueError("Invalid split/variants")
    if type(repeats) is not int or not 1 <= repeats <= 10 or not math.isfinite(timeout) or not 1 <= timeout <= 1800:
        raise ValueError("Invalid repeat count/timeout")
    if pipeline not in PIPELINES:
        raise ValueError("Invalid pipeline")
    manifest, cases = load_cases(manifest_path, project)
    selected = [c for c in cases if c["case"]["split"] == split]
    if not selected:
        raise ValueError("No cases in selected split")
    root = output_directory(output, project=project)
    # 실행 산출물이 원본이나 라벨 저장 위치를 감싸지 못하게 검증한다.
    for entry in cases:
        output_directory(root, entry["source"], project)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    out = artifact_path(root, run_id)
    out.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": "poster.benchmark-run.v1", "run_id": run_id, "split": split, "status": "running", "pipeline": pipeline,
              "manifest_sha256": sha256(manifest_path), "manifest": manifest, "environment": environment_metadata(pipeline),
              "variants": list(variants), "repeats": repeats, "timeout_seconds": timeout,
              "case_ids": [c["case"]["id"] for c in selected],
              "reserved_case_ids": [c["case"]["id"] for c in cases if c["case"]["split"] != split], "runs": [], "summary": []}
    # 미커밋 실험 코드도 해시와 함께 고정하여 후속 변경 이후 재현할 수 있게 한다.
    for name, digest in report["environment"]["code_sha256"].items():
        snapshot = out / "code.snapshot" / name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes((PROJECT_ROOT / name).read_bytes())
        if sha256(snapshot) != digest:
            raise ValueError("Experiment source changed during snapshot")
    write_json(out / "report.json", report)
    print(f"Run: {out}", flush=True)
    for entry in selected:
        case, source, label = entry["case"], entry["source"], entry["label"]
        directory = artifact_path(out, case["id"])
        directory.mkdir()
        (directory / "source.bin").write_bytes(source.read_bytes())
        if sha256(directory / "source.bin") != case["sha256"]:
            raise ValueError("Source changed after validation")
        rgb, metadata = normalize_image(directory / "source.bin")
        rgb.save(directory / "normalized.png")
        write_json(directory / "source.json", metadata)
        if label is not None:
            write_json(directory / "label.snapshot.json", label)
            # 라벨 박스는 추론 입력과 별도 이미지로 저장해 사람이 검토할 수 있게 한다.
            preview = rgb.copy()
            draw = ImageDraw.Draw(preview)
            for region in label["regions"]:
                draw.rectangle(region["bbox_xyxy"], outline="red", width=2)
                draw.text(tuple(region["bbox_xyxy"][:2]), region["id"], fill="red", stroke_width=1, stroke_fill="white")
            preview.save(directory / "label-preview.png")
        for repeat in range(repeats):
            # 반복 시 입력 순서를 순환해 순서에 따른 편향을 줄인다.
            offset = repeat % len(variants)
            order = list(variants[offset:]) + list(variants[:offset])
            for variant in order:
                attempt_dir = artifact_path(directory, f"{variant}-{repeat + 1}")
                attempt_dir.mkdir()
                prepared, transform = prepare_variant(rgb, variant)
                input_path = attempt_dir / "input.png"
                prepared.save(input_path)
                row = {"case_id": case["id"], "variant": variant, "repeat": repeat + 1,
                       "source_sha256": case["sha256"], "input_sha256": sha256(input_path),
                       "transform": transform, "artifacts": attempt_dir.relative_to(out).as_posix()}
                result = (run_worker(input_path, attempt_dir, timeout) if pipeline == "baseline"
                          else run_worker(input_path, attempt_dir, timeout, pipeline=pipeline))
                row.update({key: value for key, value in result.items() if key != "raw"})
                if result.get("status") == "success":
                    try:
                        raw = result["raw"]
                        write_json(attempt_dir / "ocr.raw.json", raw)
                        records = map_records(raw, rgb.size, transform["scale"])
                        row.update(device=raw["device"], attempts=raw.get("attempts", []), record_count=len(records))
                        write_json(attempt_dir / "ocr.normalized.json", {"coordinate_space": "normalized_original_pixels", "records": records})
                        if label is not None:
                            row["evaluation"] = evaluate(records, label, multiline=pipeline != "baseline")
                            write_json(attempt_dir / "evaluation.json", row["evaluation"])
                    except (KeyError, TypeError, ValueError) as exc:
                        row.update(status="failed", error_type=type(exc).__name__, error=str(exc))
                report["runs"].append(row)
                report["summary"] = summarize(report["runs"])
                write_json(out / "report.json", report)
                print(f"{case['id']} {variant} #{repeat + 1}: {row['status']}, device={row.get('device', 'unknown')}", flush=True)
    report["source_unchanged"] = all(sha256(e["source"]) == e["case"]["sha256"] for e in cases)
    report["status"] = "completed" if report["source_unchanged"] and all(r["status"] == "success" for r in report["runs"]) else "failed"
    write_json(out / "report.json", report)
    (out / "report.md").write_text(markdown_report(report), encoding="utf-8")
    return out, report


def main():
    """일반 실행과 내부 작업 프로세스를 구분하고 실패를 종료 코드에 반영한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/ocr-benchmarks")
    parser.add_argument("--split", choices=("tuning", "holdout"), default="tuning")
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--pipeline", choices=PIPELINES, default="baseline")
    parser.add_argument("--worker-input", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_input or args.worker_output:
        if not (args.worker_input and args.worker_output):
            parser.error("Both worker paths required")
        return worker(args.worker_input, args.worker_output, args.pipeline)
    try:
        _, report = run_benchmark(args.manifest, args.output, split=args.split, variants=args.variants,
                                  repeats=args.repeats, timeout=args.timeout, pipeline=args.pipeline)
        return 0 if report["status"] == "completed" else 1
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
