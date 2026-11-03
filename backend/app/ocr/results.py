"""Validate OCR provenance and map coordinates into the displayed image."""
import math


MULTILINE_PIPELINES = {
    "multiline_v2",
    "adaptive_v1",
    "paddle_regions_v1",
    "paddle_full_v1",
    "qwen_regions_v1",
}


RECOGNITION_ENGINES = {"easyocr", "paddleocr", "qwen3-vl"}


SELECTION_REASONS = {
    "new_model_candidate_unverified",
    "model_abstained_keep_original",
    "conflicting_candidates_keep_original",
    "stronger_candidate_unverified",
    "new_detection_unverified",
    "vlm_location_unverified",
    "same_text_without_confidence_boost",
    "refinement_incomplete",
}


def _safe_runtime(payload: dict) -> dict:
    """Return public execution metadata, never URLs, credentials or tracebacks."""
    allowed_values = {
        "engine": {"easyocr", "paddleocr", "easyocr+paddleocr", "easyocr+qwen3-vl"},
        "execution_target": {"local", "runpod"},
        "device": {"cuda", "mps", "cpu", "gpu", "runpod", "remote-auto", "unknown"},
        "status": {
            "ok",
            "ready",
            "completed",
            "failed",
            "unavailable",
            "not_ready",
            "configured",
            "success",
            "skipped",
            "fallback",
        },
    }
    result = {}
    if isinstance(payload.get("ready"), bool):
        result["ready"] = payload["ready"]
    for key, allowed in allowed_values.items():
        value = payload.get(key)
        if isinstance(value, str) and value.lower() in allowed:
            result[key] = value.lower()
    if payload.get("pipeline") in MULTILINE_PIPELINES:
        result["pipeline"] = payload["pipeline"]
    if payload.get("refinement_status") in {
        "completed",
        "partial",
        "timeout",
        "worker_failed",
    }:
        result["refinement_status"] = payload["refinement_status"]
    # Attempts deliberately omit free-form errors that may contain secrets.
    result["attempts"] = []
    for attempt in (
        payload.get("attempts", []) if isinstance(payload.get("attempts"), list) else []
    ):
        if isinstance(attempt, dict):
            clean = {}
            for key in ("device", "execution_target", "status"):
                value = attempt.get(key)
                if isinstance(value, str) and value.lower() in allowed_values[key]:
                    clean[key] = value.lower()
            if clean:
                result["attempts"].append(clean)
    return result


def _map_records(payload: dict, width: int, height: int) -> list[dict]:
    raw_records = payload.get("records")
    if not isinstance(raw_records, list) or len(raw_records) > 10_000:
        raise ValueError("Invalid OCR record list")
    expected_dimensions = [width * 2, height * 2]
    dimensions = payload.get("input_dimensions")
    if isinstance(dimensions, dict):
        dimensions = [dimensions.get("width"), dimensions.get("height")]
    if dimensions is not None and list(dimensions) != expected_dimensions:
        raise ValueError("OCR returned coordinates for an unexpected image size")
    mapped = []
    seen = set()
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
            raise ValueError("Invalid OCR text")
        record_id = raw.get("id", f"ocr-{index + 1}")
        if (
            not isinstance(record_id, (str, int))
            or isinstance(record_id, bool)
            or record_id in seen
        ):
            raise ValueError("Invalid OCR ID")
        seen.add(record_id)
        text = raw["text"].strip()
        if not text:
            continue
        if len(text) > 10_000:
            raise ValueError("OCR text is too long")
        box = raw.get("bbox_xyxy")
        if (
            not isinstance(box, (list, tuple))
            or len(box) != 4
            or any(
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not math.isfinite(number)
                for number in box
            )
        ):
            raise ValueError("Invalid OCR bounding box")
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("Degenerate OCR bounding box")
        coordinates = [
            max(0, min(limit, float(number) / 2))
            for number, limit in zip(box, [width, height, width, height])
        ]
        if coordinates[2] <= coordinates[0] or coordinates[3] <= coordinates[1]:
            raise ValueError("OCR bounding box outside image")
        confidence = raw.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError("Invalid OCR confidence")
        record = {
            "id": record_id,
            "text": text,
            "confidence": confidence,
            "bbox_xyxy": coordinates,
        }
        # 재인식 정보도 허용한 값만 공개하고 입력 좌표는 한 번만 복원한다.
        if "requires_review" in raw:
            if type(raw["requires_review"]) is not bool:
                raise ValueError("Invalid review flag")
            record["requires_review"] = raw["requires_review"]
        if "baseline_text" in raw:
            if (
                not isinstance(raw["baseline_text"], str)
                or len(raw["baseline_text"]) > 10_000
            ):
                raise ValueError("Invalid baseline text")
            record["baseline_text"] = raw["baseline_text"]
        # 원문 근거의 점수·엔진·보류 사유를 검증해 항목별 재판정에 전달한다.
        if "baseline_confidence" in raw:
            baseline_confidence = raw["baseline_confidence"]
            if baseline_confidence is not None and (
                type(baseline_confidence) not in (int, float)
                or not math.isfinite(baseline_confidence)
                or not 0 <= baseline_confidence <= 1
            ):
                raise ValueError("Invalid baseline confidence")
            record["baseline_confidence"] = baseline_confidence
        if "baseline_requires_review" in raw:
            if type(raw["baseline_requires_review"]) is not bool:
                raise ValueError("Invalid baseline review flag")
            record["baseline_requires_review"] = raw["baseline_requires_review"]
        if "baseline_recognition_engine" in raw:
            if raw["baseline_recognition_engine"] not in RECOGNITION_ENGINES:
                raise ValueError("Invalid baseline recognition engine")
            record["baseline_recognition_engine"] = raw["baseline_recognition_engine"]
        if raw.get("selection_reason") in SELECTION_REASONS:
            record["selection_reason"] = raw["selection_reason"]
        alternatives = raw.get("alternatives", [])
        if not isinstance(alternatives, list) or len(alternatives) > 10:
            raise ValueError("Invalid OCR alternatives")
        if alternatives:
            # 후보에도 같은 좌표 검증을 적용하고 임의 속성은 공개하지 않는다.
            candidates = [
                {
                    "id": f"candidate-{i}",
                    "text": c.get("text"),
                    "confidence": c.get("confidence"),
                    "bbox_xyxy": c.get("bbox_xyxy"),
                }
                for i, c in enumerate(alternatives)
            ]
            record["alternatives"] = _map_records(
                {"records": candidates}, width, height
            )
            for candidate, raw_candidate in zip(record["alternatives"], alternatives):
                if raw_candidate.get("engine") in RECOGNITION_ENGINES:
                    candidate["engine"] = raw_candidate["engine"]
        # 단독 Paddle의 출처가 빠져도 EasyOCR 기본 점수 기준으로 낮아지지 않게 한다.
        if "recognition_engine" in raw:
            if (
                not isinstance(raw["recognition_engine"], str)
                or raw["recognition_engine"] not in RECOGNITION_ENGINES
            ):
                raise ValueError("Invalid recognition engine")
            record["recognition_engine"] = raw["recognition_engine"]
        if (
            payload.get("engine") == "paddleocr"
            or payload.get("pipeline") == "paddle_full_v1"
        ):
            if record.get("recognition_engine", "paddleocr") != "paddleocr":
                raise ValueError("Paddle payload recognition engine mismatch")
            record["recognition_engine"] = "paddleocr"
        if raw.get("polygon") is not None:
            polygon = raw["polygon"]
            if (
                not isinstance(polygon, list)
                or len(polygon) < 4
                or len(polygon) > 32
                or any(
                    not isinstance(p, list)
                    or len(p) != 2
                    or any(
                        type(v) not in (int, float) or not math.isfinite(v) for v in p
                    )
                    for p in polygon
                )
            ):
                raise ValueError("Invalid OCR polygon")
            record["polygon"] = [
                [max(0, min(width, p[0] / 2)), max(0, min(height, p[1] / 2))]
                for p in polygon
            ]
        mapped.append(record)
    return mapped
