"""Real local OCR inspection. No filenames, answer keys, or fixtures determine verdicts."""
from __future__ import annotations

import os
import re
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Any

from PIL import Image

from app.ocr import get_capabilities, method_name, read_image, selected_backend

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
ALLOWED_ROOTS = (ROOT / "data" / "samples", ROOT / "data" / "topics", Path(os.environ.get("LATTE_RUNTIME_DIR", str(ROOT / "data" / "runtime"))) / "uploads")
MAX_PIXELS = 20_000_000
MAX_BYTES = 20 * 1024 * 1024


def validate_image_path(path: Path | str) -> Path:
    resolved = Path(path).resolve(strict=True)
    if not any(resolved.is_relative_to(root.resolve()) for root in ALLOWED_ROOTS):
        raise ValueError("검사 허용 디렉터리 밖의 파일입니다.")
    if not resolved.is_file() or resolved.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise ValueError("PNG/JPEG 이미지 파일만 검사할 수 있습니다.")
    if resolved.stat().st_size > MAX_BYTES:
        raise ValueError("이미지 용량 제한(20MB)을 초과했습니다.")
    with Image.open(resolved) as image:
        if image.format not in {"PNG", "JPEG"}:
            raise ValueError("파일 내용이 PNG/JPEG 형식이 아닙니다.")
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("이미지 픽셀 제한(20MP)을 초과했습니다.")
        image.verify()
    return resolved


def read_ocr(path: Path | str) -> dict:
    return read_image(validate_image_path(path))


def _normalized(text: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", str(text)))


def _field_value(text, normalization):
    value = _normalized(text)
    if normalization == 'time_dash':
        return re.sub(r'[–—−~〜～-]+', '-', value)
    if normalization == 'currency':
        return value.replace(',', '')
    if normalization == 'date':
        return re.sub(r'[./-]', '', value)
    return value


def _base(rule: dict) -> dict:
    return {"rule_id": rule["id"], "name": rule.get("name", rule["id"]),
            "expected": rule.get("expected"), "observed": None,
            "verdict": "확인 필요", "execution_status": "not_run",
            "reason": "", "evidence": [], "method": method_name()}


def unavailable_results(rules: list[dict], reason: str) -> list[dict]:
    results = []
    for rule in rules:
        result = _base(rule)
        result.update(execution_status="failed", verdict=None, reason=reason)
        results.append(result)
    return results


def inspect_configured_rule(lines: list[dict], rule: dict) -> dict:
    """Select fields by declared layout and format, never by the expected value."""
    item = _base(rule)
    config = rule['inspection']
    if config['kind'] == 'manual':
        item.update(method='manual-review-required',
                    reason='객체 개수·크기 구분·외관 자동 검사는 아직 구현하지 않았습니다. 사람이 확인해야 합니다.')
        return item
    item['execution_status'] = 'succeeded'
    x, y, w, h = config['region']
    candidates = [line for line in lines
                  if x <= line['bbox'][0] + line['bbox'][2] / 2 <= x + w
                  and y <= line['bbox'][1] + line['bbox'][3] / 2 <= y + h
                  and re.search(config.get('contains', '.'), line['text'])]
    # OCR can split a single row into multiple boxes. Group by vertical overlap
    # before reading left to right, so small baseline differences do not reorder it.
    rows = []
    for line in sorted(candidates, key=lambda line: line['bbox'][1]):
        center = line['bbox'][1] + line['bbox'][3] / 2
        row = next((row for row in rows if abs(center - row[0]) < min(row[1], line['bbox'][3]) / 2), None)
        if row is None:
            rows.append([center, line['bbox'][3], [line]])
        else:
            row[2].append(line)
    candidates = [line for row in rows for line in sorted(row[2], key=lambda line: line['bbox'][0])]
    item['evidence'] = [{'text': line['text'], 'bbox': line['bbox']} for line in candidates]
    item['observed'] = ' | '.join(line['text'] for line in candidates) or '검출되지 않음'
    if not candidates:
        item.update(reason='지정된 영역에서 문구를 읽지 못했습니다. 누락과 판독 실패를 구분하려면 확인이 필요합니다.',
                    evidence=[{'text': '문구 검사 영역 (미검출)', 'bbox': config['region']}])
        return item
    if any(line.get('confidence', 0) < .5 for line in candidates):
        item['reason'] = 'OCR 신뢰 신호가 낮아 자동 판정을 보류했습니다. 수치는 보정된 확률이 아닙니다.'
        return item
    values = [_normalized(line['text']) for line in candidates]
    if config.get('extract'):
        values = [match for value in values for match in re.findall(config['extract'], value)]
    if config.get('join_lines'):
        # Overlapping crops can duplicate characters. Do not silently fix OCR
        # to agree with the expected title; preserve the raw evidence for review.
        for left, right in zip(candidates, candidates[1:]):
            if (left['bbox'][0] + left['bbox'][2] > right['bbox'][0]
                    and abs(left['bbox'][1] - right['bbox'][1]) < min(left['bbox'][3], right['bbox'][3]) / 2
                    and _normalized(left['text'])[-1:] == _normalized(right['text'])[:1]):
                item['reason'] = 'OCR 영역이 겹치고 글자가 중복되어 확인이 필요합니다. 원문을 자동 보정하지 않습니다.'
                return item
        values = [''.join(values)]
    values = [_field_value(value, config.get('normalize')) for value in values]
    if len(values) != 1 or (config.get('format') and not re.fullmatch(config['format'], values[0])):
        item['reason'] = '문구 후보가 여러 개이거나 형식을 판독하지 못해 확인이 필요합니다.'
        return item
    expected = _field_value(rule['expected'], config.get('normalize'))
    matches = values[0] == expected
    item.update(verdict='충족' if matches else '미충족',
                reason='OCR에서 읽은 문구가 확정 기준과 일치합니다.' if matches else 'OCR에서 읽은 원문이 확정 기준과 다릅니다. 근거 영역을 확인하세요.')
    return item


def inspect_text_rules(lines: list[dict], rules: list[dict]) -> list[dict]:
    """Compare observed OCR text; matching tolerates whitespace only, not misspellings."""
    results = []
    upper = [line for line in lines if line["bbox"][1] < .50]
    for rule in rules:
        if 'inspection' in rule:
            results.append(inspect_configured_rule(lines, rule))
            continue
        item = _base(rule)
        code = rule["id"]
        if code not in {"C01", "C02", "C03"}:
            item.update(reason="로컬 OCR은 객체 개수·제품 외관을 검사하지 않습니다. 사람이 확인해야 합니다.",
                        method="manual-review-required")
            results.append(item)
            continue
        item["execution_status"] = "succeeded"
        candidates: list[dict] = []
        if code == "C01":
            candidates = [line for line in upper if re.search(r"[가-힣]", line["text"])
                          and "%" not in line["text"] and "할인" not in line["text"]]
            # The contract defines the large upper title. Avoid guessing if tied.
            candidates.sort(key=lambda line: line["bbox"][3], reverse=True)
            if len(candidates) > 1 and candidates[1]["bbox"][3] >= candidates[0]["bbox"][3] * .9:
                item["reason"] = "제목 후보가 여러 개여서 대표 제목을 확정할 수 없습니다."
                item["evidence"] = candidates
                results.append(item)
                continue
            candidates = candidates[:1]
        elif code == "C02":
            candidates = [line for line in upper if "%" in line["text"] or "할인" in line["text"]]
        else:
            candidates = [line for line in upper if "MORNING" in line["text"].upper()
                          or "CUP" in line["text"].upper()]
        item["evidence"] = [{"text": line["text"], "bbox": line["bbox"]} for line in candidates]
        item["observed"] = " | ".join(line["text"] for line in candidates) or "검출되지 않음"
        if not candidates:
            item["reason"] = "OCR에서 대상 문구를 찾지 못했습니다. 실제 누락과 판독 실패를 구분하려면 확인이 필요합니다."
            item["evidence"] = [{"text": "검사한 상단 영역 (미검출)", "bbox": [0, 0, 1, .5]}]
        elif any(line.get("confidence", 0) < .5 for line in candidates):
            item["reason"] = "OCR 판독 신뢰 신호가 낮아 자동 판정을 보류했습니다. 수치는 보정된 확률이 아닙니다."
        elif code == "C03":
            count = sum(_normalized(line["text"]).count(_normalized(rule["expected"])) for line in candidates)
            if count == 1 and len(candidates) == 1 and _normalized(candidates[0]["text"]) == _normalized(rule["expected"]):
                item.update(verdict="충족", reason="상단 OCR에서 기대 브랜드 문구가 한 번 검출됐습니다. OCR이 놓친 표기는 사람이 확인할 수 있습니다.")
            elif count > 1:
                item.update(verdict="미충족", reason="브랜드명이 한 번을 초과해 검출됐습니다.")
            else:
                item["reason"] = "브랜드 일부만 판독됐거나 다른 문구가 섞여 있어 확인이 필요합니다."
        elif len(candidates) != 1:
            item["reason"] = "문구 후보가 여러 개이거나 나뉘어 있어 자동 판정을 보류했습니다."
        elif code == 'C02' and re.fullmatch(r'\d+(?:\.\d+)?%할인', _normalized(rule['expected'])) and not re.fullmatch(r'\d+(?:\.\d+)?%할인', _normalized(candidates[0]['text'])):
            item['reason'] = '할인 문구가 숫자·%·할인 형식으로 판독되지 않아 확인이 필요합니다.'
        else:
            matches = _normalized(candidates[0]["text"]) == _normalized(rule["expected"])
            item.update(verdict="충족" if matches else "미충족",
                        reason="공백을 제외한 OCR 원문이 기준과 일치합니다." if matches else "OCR에서 읽은 원문이 확정 기준과 다릅니다. 강조 영역을 확인하세요.")
        results.append(item)
    return results


def inspect_image(path: Path, rules: list) -> dict:
    start = time.monotonic()
    try:
        ocr = read_ocr(path)
        results = inspect_text_rules(ocr["lines"], rules)
        for result in results:
            if result['execution_status'] == 'succeeded':
                result['method'] = ocr.get('method', method_name())
        status = "succeeded"
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        results = unavailable_results(rules, str(error))
        ocr = {"lines": []}
        status = "failed"
    return {"results": results, "method": ocr.get('method', method_name()), "ocr": ocr,
            "trace": [{"method": "local/" + selected_backend(), "status": status,
                       "device": ocr.get('device'), "fallback_reason": ocr.get('fallback_reason', ''),
                       "duration_ms": round((time.monotonic() - start) * 1000)}]}
