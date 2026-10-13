"""로컬 EasyOCR의 제한된 영역 재인식. 외부 기대 문구·라벨·파일명은 추론에 사용하지 않는다."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .event_layout import extract_event_multiline
from .ocr_runtime import OCRRuntimeError, PROJECT_ROOT

MAX_CROPS = 8
RETRY_SECONDS = 25
HARD_TIMEOUT_SECONDS = 60


def save_json(path, payload):
    """부분 완료 결과를 원자적으로 저장해 시간 초과 시 원래 OCR을 복구한다."""
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def compact(text):
    """공백·유니코드만 통일해 후보 일치 여부를 비교한다."""
    import unicodedata
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text))


def iou(a, b):
    """겹치는 탐지 영역의 교집합 비율을 계산한다."""
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    total = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / total if total > 0 else 0


def select_regions(records):
    """핵심 정보는 높은 점수도 재확인하고, 나머지는 낮은 점수·큰 글자부터 제한한다."""
    event = extract_event_multiline(records)
    core = {i for name in ("event_name", "start_date", "location_raw") for i in event["fields"][name]["source_ocr_ids"]}
    missing = any(event["fields"][name]["value"] is None for name in ("event_name", "start_date", "location_raw"))
    ranked = []
    for record in records:
        low = record.get("confidence") is None or record["confidence"] < .7
        if len(compact(record["text"])) < 2 or not (low or record["id"] in core):
            continue
        reasons = (["core_field_verification"] if record["id"] in core else []) + (["low_confidence"] if low else [])
        if missing:
            reasons.append("missing_or_conflicting_fields")
        ranked.append((record, reasons))
    ranked.sort(key=lambda pair: (pair[0]["id"] not in core,
                                 -(pair[0]["bbox_xyxy"][3] - pair[0]["bbox_xyxy"][1])))
    return ranked[:MAX_CROPS], missing


def prepare_crop(rgb, box):
    """글자 높이에 비례한 여백·확대·대비를 적용하고 역변환 정보를 반환한다."""
    from PIL import Image, ImageOps
    if len(box) != 4 or any(not math.isfinite(v) for v in box) or not (0 <= box[0] < box[2] <= rgb.width and 0 <= box[1] < box[3] <= rgb.height):
        raise ValueError("Invalid crop box")
    padding = max(2, (box[3] - box[1]) * .12)
    crop_box = [max(0, math.floor(box[0] - padding)), max(0, math.floor(box[1] - padding)),
                min(rgb.width, math.ceil(box[2] + padding)), min(rgb.height, math.ceil(box[3] + padding))]
    crop = rgb.crop(crop_box)
    scale = min(2., 2048 / crop.width, 512 / crop.height)
    size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    crop = ImageOps.autocontrast(ImageOps.grayscale(crop.resize(size, Image.Resampling.LANCZOS))).convert("RGB")
    sx, sy = size[0] / (crop_box[2] - crop_box[0]), size[1] / (crop_box[3] - crop_box[1])
    transform = {"crop_box_input": crop_box, "scale_xy": [sx, sy],
                 "crop_to_input_matrix": [[1 / sx, 0, crop_box[0]], [0, 1 / sy, crop_box[1]], [0, 0, 1]],
                 "preprocessing": "padding 0.12 text height; Lanczos; grayscale autocontrast"}
    return crop, transform


def restore_candidate(candidate, transform, identifier):
    """crop 내 인식 박스·다각형을 상위 OCR 입력 좌표로 돌려놓는다."""
    sx, sy = transform["scale_xy"]
    x, y, _, _ = transform["crop_box_input"]
    points = [[px / sx + x, py / sy + y] for px, py in candidate["polygon"]]
    return {**candidate, "id": identifier, "polygon": points,
            "bbox_xyxy": [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]}


def choose_candidate(original, candidate, attempt_id):
    """불일치 후보는 자동 확정하지 않고 원문·새 후보와 선택 사유를 모두 남긴다."""
    result = copy.deepcopy(original)
    result.setdefault("baseline_text", original["text"])
    alternatives = result.setdefault("alternatives", [])
    alternatives.append({"text": candidate["text"], "confidence": candidate["confidence"],
                         "bbox_xyxy": candidate["bbox_xyxy"], "attempt_id": attempt_id})
    same = compact(original["text"]) == compact(candidate["text"])
    if same:
        result["confidence"] = min(original.get("confidence") or 0, candidate.get("confidence") or 0)
        result["selection_reason"] = "same_text_without_confidence_boost"
    else:
        # 높은 점수라도 기존 후보와의 충돌은 사람이 확인한다.
        result["requires_review"] = True
        result["selection_reason"] = "conflicting_candidates_keep_original"
        if (original.get("confidence") or 0) < .6 and (candidate.get("confidence") or 0) >= .75:
            result.update(text=candidate["text"], confidence=candidate["confidence"],
                          selection_reason="stronger_candidate_unverified")
    return result


def read_pixels(rgb, *, region):
    """동일 한국어 모델로 영역 beamsearch 또는 전체 탐지 재검토를 실행한다."""
    import numpy as np
    from .ocr_runtime import _local_operation, _records
    pixels = np.asarray(rgb)[:, :, ::-1].copy()
    if region:
        operation = lambda reader: reader.recognize(pixels, horizontal_list=[[0, rgb.width, 0, rgb.height]], free_list=[],
                                                     decoder="beamsearch", beamWidth=5, detail=1, paragraph=False,
                                                     batch_size=1, workers=0, reformat=True)
    else:
        operation = lambda reader: reader.readtext(pixels, decoder="beamsearch", beamWidth=5, detail=1,
                                                    paragraph=False, batch_size=1, workers=0,
                                                    text_threshold=.5, low_text=.3, add_margin=.15)
    observations, device, attempts = _local_operation(operation, allow_download=False)
    return {"records": _records(observations, rgb.width, rgb.height), "device": device, "attempts": attempts,
            "settings": {"decoder": "beamsearch", "beamWidth": 5, "region_recognition": region}}


def mark_unverified(records):
    """재검토 중단·실패 결과를 성공적인 재확인으로 사용하지 않게 한다."""
    return [{**r, "requires_review": True} for r in records]


def model_identity():
    """실제 패키지 버전과 로컬 가중치 해시를 추론 결과에 연결한다."""
    import importlib.metadata
    from .ocr_runtime import MODEL_FILES, _model_dir
    models = []
    for name in MODEL_FILES:
        with (_model_dir() / name).open("rb") as stream:
            models.append({"name": name, "sha256": hashlib.file_digest(stream, "sha256").hexdigest()})
    return {"engine": "easyocr", "version": importlib.metadata.version("easyocr"),
            "torch_version": importlib.metadata.version("torch"), "models": models}


def refine(image_path, baseline, directory, *, read=read_pixels, budget=RETRY_SECONDS):
    """최대 전체 1회·crop 8회로 재검토하며 각 시도 원문과 이미지를 보존한다."""
    from .ocr_runtime import _read_image
    rgb = _read_image(image_path)
    result = copy.deepcopy(baseline)
    result.update(pipeline="adaptive_v1", baseline_records=copy.deepcopy(baseline["records"]), refinements=[])
    selected, missing = select_regions(result["records"])
    started = time.monotonic()
    result["retry_policy"] = {"max_crops": MAX_CROPS, "max_full_image": 1, "retry_budget_seconds": budget,
                              "hard_timeout_seconds": HARD_TIMEOUT_SECONDS}
    if missing and time.monotonic() - started < budget:
        attempt = {"id": "full-1", "kind": "full_image", "reason": "missing_or_conflicting_fields"}
        tick = time.monotonic()
        try:
            raw = read(rgb, region=False)
            save_json(directory / "full-1.json", raw)
            original = result["records"]
            for candidate in raw["records"]:
                overlaps = [(index, iou(r["bbox_xyxy"], candidate["bbox_xyxy"])) for index, r in enumerate(original)]
                matches = [index for index, overlap in overlaps if overlap >= .5]
                # 분할·병합된 박스는 한 줄의 교체 근거로 쓰지 않는다.
                if len(matches) == 1 and sum(iou(original[matches[0]]["bbox_xyxy"], c["bbox_xyxy"]) >= .5 for c in raw["records"]) == 1:
                    index = matches[0]
                    original[index] = choose_candidate(original[index], candidate, attempt["id"])
                elif not any(overlap > .15 for _, overlap in overlaps) and candidate["confidence"] >= .7:
                    original.append({**candidate, "id": f"full_{len(original):03d}", "requires_review": True,
                                     "selection_reason": "new_detection_unverified"})
            attempt.update(status="success", device=raw["device"], attempts=raw.get("attempts", []))
        except Exception as exc:
            attempt.update(status="failed", error_type=type(exc).__name__)
            result["records"] = mark_unverified(result["records"])
        attempt["elapsed_seconds"] = time.monotonic() - tick
        result["refinements"].append(attempt)
    # 최초 탐지에서 선택한 영역만 처리하므로 새 후보 때문에 재시도 수가 늘지 않는다.
    for source, reasons in selected:
        index = next(i for i, r in enumerate(result["records"]) if r["id"] == source["id"])
        if time.monotonic() - started >= budget:
            result["records"][index]["requires_review"] = True
            result["refinements"].append({"id": f"crop-{source['id']}", "status": "skipped", "reason": "retry_budget_exhausted"})
            continue
        crop, transform = prepare_crop(rgb, source["bbox_xyxy"])
        attempt_id = f"crop-{len(result['refinements']):02d}"
        crop_path = directory / f"{attempt_id}.png"
        crop.save(crop_path)
        attempt = {"id": attempt_id, "kind": "crop", "source_ocr_ids": [source["id"]], "reasons": reasons,
                   "crop_sha256": hashlib.sha256(crop_path.read_bytes()).hexdigest(), "transform": transform,
                   "input_dimensions": list(crop.size)}
        tick = time.monotonic()
        try:
            raw = read(crop, region=True)
            save_json(directory / f"{attempt_id}.json", raw)
            if len(raw["records"]) != 1 or not compact(raw["records"][0]["text"]):
                raise ValueError("Region must yield one nonempty candidate")
            candidate = restore_candidate(raw["records"][0], transform, source["id"])
            result["records"][index] = choose_candidate(result["records"][index], candidate, attempt_id)
            attempt.update(status="success", device=raw["device"], attempts=raw.get("attempts", []))
        except Exception as exc:
            result["records"][index]["requires_review"] = True
            attempt.update(status="failed", error_type=type(exc).__name__)
        attempt["elapsed_seconds"] = time.monotonic() - tick
        result["refinements"].append(attempt)
        save_json(directory / "progress.json", result)
    result["refinement_status"] = "completed" if all(a["status"] == "success" for a in result["refinements"]) else "partial"
    result["devices"] = sorted({baseline["device"], *(a["device"] for a in result["refinements"] if "device" in a)}) if "device" in baseline else []
    successful = [a["device"] for a in result["refinements"] if a.get("status") == "success"]
    if successful:
        result["device"] = successful[-1]
    return result


def worker(image_path, directory):
    """격리된 프로세스에서 모델을 로드하고 기준선·재인식 결과를 단계별 저장한다."""
    from .ocr_runtime import warmup_models, recognize_image
    os.environ.update(RUN_TARGET="local", OCR_ALLOW_DOWNLOAD="0")
    started = time.perf_counter()
    warmup = warmup_models(allow_download=False)
    loaded = time.perf_counter()
    baseline = recognize_image(image_path)
    baseline.update(model_identity=model_identity(), input_sha256=hashlib.sha256(Path(image_path).read_bytes()).hexdigest())
    save_json(directory / "baseline.json", baseline)
    result = refine(image_path, baseline, directory)
    import torch
    for device in {baseline["device"], *(a.get("device") for a in result["refinements"])}:
        if device in {"cuda", "mps"}:
            getattr(torch, device).synchronize()
    result["profile"] = {"load_seconds": loaded - started, "inference_seconds": time.perf_counter() - loaded,
                         "warmup": warmup}
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result["profile"]["peak_rss_bytes"] = rss if sys.platform == "darwin" else rss * 1024
    except ImportError:
        result["profile"]["peak_rss_bytes"] = None
    # 이 실행의 코드와 입력으로 재현할 수 있도록 사용한 설정도 함께 보존한다.
    save_json(directory / "result.json", result)


def recognize_adaptive(image_path, directory, *, timeout=HARD_TIMEOUT_SECONDS):
    """시간 제한이 있는 자식 프로세스로 실행하고 실패 시 저장된 기준선만 복구한다."""
    if os.getenv("RUN_TARGET", "local").strip().lower() != "local":
        raise OCRRuntimeError("Adaptive OCR currently requires RUN_TARGET=local")
    if not 1 <= timeout <= HARD_TIMEOUT_SECONDS:
        raise ValueError("Invalid adaptive OCR timeout")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, "RUN_TARGET": "local", "OCR_ALLOW_DOWNLOAD": "0"}
    reason = None
    with (directory / "worker.log").open("w", encoding="utf-8") as log:
        try:
            subprocess.run([sys.executable, "-m", "backend.app.adaptive_ocr", "--input", str(Path(image_path).resolve()),
                            "--output", str(directory.resolve())], cwd=PROJECT_ROOT, env=env, check=True,
                           timeout=timeout, stdout=log, stderr=subprocess.STDOUT)
        except subprocess.TimeoutExpired:
            reason = "timeout"
        except subprocess.CalledProcessError:
            reason = "worker_failed"
    if reason is None:
        return json.loads((directory / "result.json").read_text(encoding="utf-8"))
    baseline_path = directory / "baseline.json"
    if not baseline_path.is_file():
        raise OCRRuntimeError("Adaptive OCR failed before the baseline was ready", code=reason)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline.update(pipeline="adaptive_v1", baseline_records=copy.deepcopy(baseline["records"]),
                    records=mark_unverified(baseline["records"]), refinement_status=reason,
                    refinements=[{"id": "worker", "status": "failed", "reason": reason}])
    save_json(directory / "fallback.json", baseline)
    return baseline


def main():
    """내부 작업용 인자를 읽으며 원래 파일명이나 정답 입력은 받지 않는다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    worker(args.input, args.output)


if __name__ == "__main__":
    main()
