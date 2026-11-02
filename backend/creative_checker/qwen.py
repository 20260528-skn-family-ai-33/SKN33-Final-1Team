"""Bounded real vLLM/OpenAI-compatible HTTP calls; no alternate provider or mocks."""

from __future__ import annotations

import io
import json
import math
import re
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from . import prompts
from .inspection import validate_results

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 2_000_000
MAX_IMAGE_TOTAL_BYTES = 16 * 1024 * 1024
MAX_CRITERIA_PER_CALL = prompts.MAX_CRITERIA
EXTRACTION_CHUNK_CHARACTERS = 32_000
MAX_EXTRACTION_CHUNKS = 4
MAX_HTTP_CALLS = 64


class QwenError(RuntimeError):
    """Only sanitized messages/codes cross this boundary."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class QwenInputError(QwenError):
    pass


class QwenRequestError(QwenError):
    pass


class QwenFormatError(QwenError):
    pass


class QwenLimitError(QwenError):
    pass


def _reject_constant(_):
    raise ValueError("nonfinite JSON number")


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON property")
        value[key] = item
    return value


def _json(value):
    return json.loads(value, parse_constant=_reject_constant, object_pairs_hook=_unique_pairs)


def _endpoint(base_url: str) -> str:
    try:
        if not isinstance(base_url, str) or any(ord(char) < 33 for char in base_url):
            raise ValueError
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError
        parsed.port  # Force validation; never include the original URL in an error.
        path = parsed.path.rstrip("/")
        if not path.endswith("/chat/completions"):
            path += "/chat/completions" if path.endswith("/v1") else "/v1/chat/completions"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    except (TypeError, ValueError, AttributeError):
        raise QwenInputError("invalid_qwen_endpoint", "Qwen HTTP 주소 설정이 올바르지 않습니다.") from None


def _bounded_json(value, *, limit=prompts.MAX_INPUT_CHARACTERS):
    try:
        encoded = prompts.json_text(value)
    except (TypeError, ValueError, RecursionError):
        raise QwenInputError("invalid_input_json", "Qwen 입력은 유한한 JSON 값이어야 합니다.") from None
    if len(encoded) > limit:
        raise QwenInputError("qwen_input_limit", "Qwen 입력 텍스트가 제한을 넘습니다.")
    return encoded


def _expected_is_grounded(expected, quote: str, kind: str) -> bool:
    unit = ""
    if isinstance(expected, dict):
        if set(expected) - {"value", "unit"} or "value" not in expected or not isinstance(expected.get("unit", ""), str):
            return False
        unit, expected = expected.get("unit", ""), expected["value"]
    if isinstance(expected, bool) or not isinstance(expected, (str, int, float)):
        return False
    if isinstance(expected, float) and not math.isfinite(expected):
        return False
    text = str(expected)
    if not text.strip():
        return False
    expression = re.escape(text) + (r"\s*" + re.escape(unit) if unit else "")
    if text[0].isdigit():
        expression = r"(?<![\d,.])" + expression
    if (unit or text)[-1].isdigit():
        expression += r"(?![\d,.])"
    matches = list(re.finditer(expression, quote))
    if not matches:
        return False
    if kind in {"amount", "quantity"} and not unit and re.fullmatch(r"[+-]?[\d,.]+", text):
        # The model may not silently remove a stated currency/quantity unit.
        return any(not re.match(r"\s*[A-Za-z가-힣%‰㎡²³]", quote[match.end():]) and not re.search(r"[₩$€£]\s*$", quote[:match.start()]) for match in matches)
    return True


def _mark_unresolved_scopes(extracted: dict, blocks: list[dict]):
    """Do not turn an ungrounded logical group, condition or exception into a decision."""
    items = {item["id"]: item for item in extracted["items"]}
    grouped = set()
    for group in extracted["groups"]:
        leaves = [items[key] for key in group["criterion_ids"]]
        marker = r"(?:또는|혹은|중\s*하나|택일|\bOR\b)" if group["operator"] == "any" else r"(?:그리고|모두|동시에|각각|\s및\s|\bAND\b)"
        grounded = any(
            re.search(marker, block["text"], re.I)
            and all(leaf["source"]["block_id"] == block["id"] and leaf["source"]["quote"] in block["text"] for leaf in leaves)
            for block in blocks
        )
        if not grounded:
            group["condition"] = {"status": "unverified_scope", "source_condition": group["condition"]}
        grouped.update(group["criterion_ids"])
    for block in blocks:
        has_scope = re.search(r"또는|혹은|예외|제외|다만|단,|경우|\b(?:OR|unless|except|if)\b", block["text"], re.I)
        if has_scope:
            for item in extracted["items"]:
                if item["source"]["block_id"] == block["id"] and item["id"] not in grouped:
                    item["supported"] = False
                    item["notes"] = (item["notes"] + "\n원문에 조건·대안·예외가 있어 적용 범위를 확인해야 합니다.").strip()


def _normalise_images(images: list, *, role: str) -> list[dict]:
    limit = prompts.MAX_FRAME_INPUTS if role == "asset" else prompts.MAX_LOGOS
    if not isinstance(images, list) or len(images) > limit:
        raise QwenInputError("image_count_limit", "Qwen 이미지 입력 수가 제한을 넘습니다.")
    normalised = []
    seen = set()
    for index, source in enumerate(images):
        if not isinstance(source, dict) or not isinstance(source.get("data"), bytes):
            raise QwenInputError("invalid_image_input", "Qwen 이미지에는 실제 bytes 데이터가 필요합니다.")
        data = source["data"]
        byte_limit = 20 * 1024 * 1024 if role == "logo" else MAX_IMAGE_BYTES
        pixel_limit = 10_000_000 if role == "logo" else MAX_IMAGE_PIXELS
        if not data or len(data) > byte_limit:
            raise QwenInputError("image_input_size_limit", "Qwen 이미지 입력 크기가 제한을 넘습니다.")
        mime = source.get("media_type")
        file_id = source.get("file_id", f"{role}_{index + 1:04d}")
        timestamp = source.get("frame_ms")
        if not isinstance(file_id, str) or not file_id or len(file_id) > 160 or any(ord(char) < 32 for char in file_id):
            raise QwenInputError("invalid_file_id", "Qwen 이미지 근거 ID가 올바르지 않습니다.")
        if timestamp is not None and (isinstance(timestamp, bool) or not isinstance(timestamp, int) or not 0 <= timestamp < 60_000):
            raise QwenInputError("invalid_frame_time", "Qwen 프레임 시각이 올바르지 않습니다.")
        if role == "logo" and timestamp is not None:
            raise QwenInputError("invalid_logo_time", "기준 로고에는 영상 프레임 시각을 지정할 수 없습니다.")
        identity = (file_id, timestamp)
        if identity in seen:
            raise QwenInputError("duplicate_image_provenance", "같은 파일·프레임 근거가 중복됩니다.")
        seen.add(identity)
        try:
            with Image.open(io.BytesIO(data)) as image:
                actual = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(image.format)
                if actual is None or actual != mime or image.width * image.height > pixel_limit or getattr(image, "n_frames", 1) != 1:
                    raise ValueError
                image.verify()
            if role == "logo":
                # The reference FileRecord remains the source. Uniform resize and EXIF
                # orientation preserve its normalized region coordinates, without crops.
                with Image.open(io.BytesIO(data)) as image:
                    oriented = ImageOps.exif_transpose(image)
                    oriented.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                    rgba = oriented.convert("RGBA")
                    rgb = Image.new("RGB", rgba.size, "white")
                    rgb.paste(rgba, mask=rgba.getchannel("A"))
                    stream = io.BytesIO()
                    rgb.save(stream, format="JPEG", quality=92)
                    data, mime = stream.getvalue(), "image/jpeg"
                    rgba.close()
                    rgb.close()
                    if oriented is not image:
                        oriented.close()
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            raise QwenInputError("invalid_derived_image", "Qwen 입력은 형식이 일치하는 200만 픽셀 이하의 정지 이미지여야 합니다.") from None
        view_role = source.get("role", "frame" if timestamp is not None else "full") if role == "asset" else "logo"
        if view_role not in {"asset", "full", "crop", "frame", "logo"} or (role == "asset" and view_role == "logo"):
            raise QwenInputError("invalid_image_role", "이미지 역할이 올바르지 않습니다.")
        item = {"data": data, "media_type": mime, "file_id": file_id, "frame_ms": timestamp, "role": view_role, "source_role": role}
        if "source_bbox" in source:
            box = source["source_bbox"]
            if not isinstance(box, (list, tuple)) or len(box) != 4 or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in box):
                raise QwenInputError("invalid_source_bbox", "파생 이미지의 원본 영역 매핑이 올바르지 않습니다.")
            x, y, width, height = box
            if min(x, y) < 0 or min(width, height) <= 0 or x + width > 1.000001 or y + height > 1.000001:
                raise QwenInputError("invalid_source_bbox", "파생 이미지의 원본 영역 매핑이 올바르지 않습니다.")
            item["source_bbox"] = list(box)
        if isinstance(source.get("asset_id"), str):
            item["asset_id"] = source["asset_id"]
        normalised.append(item)
    return normalised


class QwenClient:
    """Synchronous client. max_calls counts EVERY HTTP attempt, including repairs/retries.

    stats exposes usage (not estimated currency), safe events and cumulative HTTP time.
    A client belongs to one bounded workflow; create another for an explicitly new run.
    """

    def __init__(self, base_url, api_key, model=prompts.MODEL, timeout=120, max_calls=32):
        if model != prompts.MODEL:
            raise QwenInputError("unsupported_qwen_model", "이 워크플로는 Qwen/Qwen3.5-4B 모델만 사용합니다.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise QwenInputError("invalid_timeout", "Qwen 요청 제한 시간은 0초 초과 120초 이하여야 합니다.")
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or not 1 <= max_calls <= MAX_HTTP_CALLS:
            raise QwenInputError("invalid_call_limit", "Qwen HTTP 호출 한도는 1~64여야 합니다.")
        if api_key is not None and (not isinstance(api_key, str) or any(ord(char) < 33 or ord(char) > 126 for char in api_key)):
            raise QwenInputError("invalid_api_key", "Qwen 인증 설정이 올바르지 않습니다.")
        self.model = model
        self.timeout = float(timeout)
        self.max_calls = max_calls
        self._url = _endpoint(base_url)
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.Client(headers=headers, follow_redirects=False, trust_env=False, timeout=httpx.Timeout(timeout, connect=min(timeout, 10), pool=min(timeout, 10)))
        self._deadline: float | None = None
        self._closed = False
        self.stats = {
            "calls": 0, "max_calls": max_calls, "network_retries": 0, "format_repairs": 0,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "usage_complete": True, "cost": None, "http_seconds": 0.0, "events": [],
        }

    def close(self):
        self._closed = True
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _event(self, event: str, stage: str, **fields):
        # Only locally constructed enum/numeric metadata. No response/request text or URLs.
        self.stats["events"].append({"event": event, "stage": stage, **fields})

    def _budget(self) -> float:
        if self._closed:
            raise QwenRequestError("client_closed", "Qwen 클라이언트가 닫혔습니다.")
        if self.stats["calls"] >= self.max_calls:
            raise QwenLimitError("qwen_call_limit", "Qwen HTTP 호출 한도에 도달했습니다.")
        if self._deadline is None:
            self._deadline = time.monotonic() + 600
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise QwenLimitError("qwen_time_limit", "Qwen 워크플로 제한 시간 600초에 도달했습니다.")
        return min(self.timeout, remaining)

    def _usage(self, envelope: dict):
        usage = envelope.get("usage")
        if not isinstance(usage, dict):
            self.stats["usage_complete"] = False
            return
        for key in self.stats["usage"]:
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                self.stats["usage"][key] += value
            else:
                self.stats["usage_complete"] = False

    def _http_request(self, payload: dict, stage: str) -> dict:
        for attempt in range(3):  # First request plus at most two transient/network retries.
            allowed_seconds = self._budget()
            self.stats["calls"] += 1
            started = time.monotonic()
            try:
                with self._http.stream("POST", self._url, json=payload, timeout=httpx.Timeout(allowed_seconds, connect=min(10, allowed_seconds), pool=min(10, allowed_seconds))) as response:
                    status = response.status_code
                    if status == 429 or 500 <= status <= 599:
                        self._event("transient_http_error", stage, status=status, attempt=attempt + 1)
                        self.stats["usage_complete"] = False
                        retry = True
                    elif status != 200:
                        self.stats["usage_complete"] = False
                        self._event("http_error", stage, status=status)
                        raise QwenRequestError("qwen_http_error", f"Qwen 요청이 거절되었습니다(HTTP {status}).")
                    else:
                        retry = False
                        chunks = bytearray()
                        for chunk in response.iter_bytes():
                            if time.monotonic() - started > allowed_seconds:
                                raise QwenLimitError("qwen_request_timeout", "Qwen 요청 제한 시간을 넘었습니다.")
                            chunks.extend(chunk)
                            if len(chunks) > MAX_RESPONSE_BYTES:
                                self.stats["usage_complete"] = False
                                raise QwenFormatError("qwen_response_limit", "Qwen 응답 크기가 제한을 넘었습니다.")
                        try:
                            envelope = _json(chunks)
                        except (ValueError, UnicodeError, RecursionError):
                            self.stats["usage_complete"] = False
                            raise QwenFormatError("qwen_envelope_invalid", "Qwen HTTP 응답이 올바른 JSON이 아닙니다.") from None
                        if not isinstance(envelope, dict):
                            self.stats["usage_complete"] = False
                            raise QwenFormatError("qwen_envelope_invalid", "Qwen HTTP 응답 객체가 올바르지 않습니다.")
                        self._usage(envelope)
                        self._event("response_received", stage, status=status, attempt=attempt + 1)
                        return envelope
            except httpx.TransportError:
                retry = True
                self.stats["usage_complete"] = False
                self._event("network_error", stage, attempt=attempt + 1)
            finally:
                self.stats["http_seconds"] = round(self.stats["http_seconds"] + time.monotonic() - started, 4)
            if retry and attempt < 2:
                self._budget()
                self.stats["network_retries"] += 1
                time.sleep(0.25 * (attempt + 1))
            else:
                raise QwenRequestError("qwen_network_failure", "Qwen 네트워크 요청이 재시도 한도 내에 완료되지 않았습니다.") from None
        raise QwenRequestError("qwen_network_failure", "Qwen 요청이 완료되지 않았습니다.")

    def _structured(self, payload: dict, schema, stage: str, provenance=None) -> dict:
        for repair in range(2):
            try:
                envelope = self._http_request(payload, stage)
                choices = envelope.get("choices")
                if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                    raise ValueError
                choice = choices[0]
                message = choice.get("message")
                if choice.get("finish_reason") != "stop" or not isinstance(message, dict) or message.get("refusal"):
                    raise ValueError
                content = message.get("content")
                if not isinstance(content, str):
                    raise ValueError
                parsed = schema.model_validate(_json(content)).model_dump(mode="json")
                if provenance:
                    provenance(parsed)
                return parsed
            except (QwenFormatError, ValidationError, ValueError, TypeError, KeyError, RecursionError):
                self._event("invalid_format_or_provenance", stage, repair=repair)
                if repair:
                    raise QwenFormatError("qwen_invalid_output", "Qwen 응답의 스키마 또는 출처를 보정 후에도 검증하지 못했습니다.") from None
                self._budget()
                self.stats["format_repairs"] += 1
                payload = prompts.build_repair_request(payload, schema=schema)
        raise QwenFormatError("qwen_invalid_output", "Qwen 응답을 검증하지 못했습니다.")

    def extract_criteria(self, blocks) -> dict:
        if not isinstance(blocks, list) or not blocks or len(blocks) > 100_000:
            raise QwenInputError("invalid_document_blocks", "조건 추출에는 실제 문서 블록이 필요합니다.")
        block_map = {}
        chunks: list[list[dict]] = []
        current: list[dict] = []
        count = 0
        total = 0
        for block in blocks:
            if not isinstance(block, dict) or not isinstance(block.get("id"), str) or not block["id"] or len(block["id"]) > 100 or not isinstance(block.get("text"), str):
                raise QwenInputError("invalid_document_blocks", "문서 블록 ID와 텍스트가 올바르지 않습니다.")
            if block["id"] in block_map or (block.get("page") is not None and (isinstance(block["page"], bool) or not isinstance(block["page"], int) or not 1 <= block["page"] <= 50)):
                raise QwenInputError("invalid_document_blocks", "문서 블록 ID 또는 페이지가 올바르지 않습니다.")
            block_map[block["id"]] = block
            total += len(block["text"])
            if total > 100_000:
                raise QwenInputError("document_text_limit", "문서 추출량은 10만 자 이하여야 합니다.")
            for offset in range(0, len(block["text"]), EXTRACTION_CHUNK_CHARACTERS):
                text = block["text"][offset:offset + EXTRACTION_CHUNK_CHARACTERS]
                segment = {"id": block["id"], "page": block.get("page"), "kind": block.get("kind", "paragraph"), "text": text}
                size = len(_bounded_json(segment))
                if current and count + size > EXTRACTION_CHUNK_CHARACTERS + 1000:
                    chunks.append(current)
                    current, count = [], 0
                current.append(segment)
                count += size
        if current:
            chunks.append(current)
        if not chunks or len(chunks) > MAX_EXTRACTION_CHUNKS:
            raise QwenInputError("document_chunk_limit", "문서 조건 추출 입력 묶음이 제한을 넘습니다.")
        if len(chunks) > self.max_calls - self.stats["calls"]:
            raise QwenLimitError("qwen_call_limit", "문서 전체를 추출할 최소 호출 예산이 부족합니다.")
        items, groups = [], []
        for chunk in chunks:
            chunk_map = {block["id"]: block for block in chunk}

            def provenance(parsed):
                ids = [item["id"] for item in parsed["items"]]
                if len(ids) != len(set(ids)):
                    raise ValueError
                for item in parsed["items"]:
                    source = item["source"]
                    block = chunk_map.get(source["block_id"])
                    if block is None or source["page"] != block["page"] or source["quote"] not in block["text"] or item["text"] != source["quote"] or item["asset_ids"]:
                        raise ValueError
                    if item["kind"] == "unsupported" and item["supported"]:
                        raise ValueError
                    if item["supported"] and item["kind"] in {"exact_text", "date", "amount", "quantity"}:
                        if not _expected_is_grounded(item["expected"], source["quote"], item["kind"]):
                            raise ValueError
                group_ids = [group["id"] for group in parsed["groups"]]
                if len(group_ids) != len(set(group_ids)):
                    raise ValueError
                for group in parsed["groups"]:
                    if not set(group["criterion_ids"]).issubset(ids) or len(set(group["criterion_ids"])) != len(group["criterion_ids"]):
                        raise ValueError
                    for condition in (group["condition"], group["exception"]):
                        if condition and not any(condition in block["text"] for block in chunk):
                            raise ValueError

            payload = prompts.build_extraction_request(chunk, model=self.model)
            extracted = self._structured(payload, prompts.CriteriaResponse, "extract_criteria", provenance)
            _mark_unresolved_scopes(extracted, chunk)
            remap = {item["id"]: str(uuid4()) for item in extracted["items"]}
            for item in extracted["items"]:
                item["id"] = remap[item["id"]]
                if len(chunks) > 1:
                    # Cross-chunk AND/OR/exception scope cannot be established by independent calls.
                    item["supported"] = False
                    item["notes"] = (item["notes"] + "\n문서를 분할 추출했습니다. 구간을 넘는 조건·예외·그룹 범위를 사람이 확인해야 합니다.").strip()
                # Validate against the owning API contract without letting its whitespace
                # normalization change source quotes or criterion wording.
                from .contracts import Criterion
                try:
                    Criterion.model_validate(item)
                except ValidationError:
                    raise QwenFormatError("criterion_contract_invalid", "추출 조건이 공용 Criterion 계약에 맞지 않습니다.") from None
                items.append(item)
            for group in extracted["groups"]:
                group["id"] = str(uuid4())
                group["criterion_ids"] = [remap[key] for key in group["criterion_ids"]]
                groups.append(group)
            if len(items) > prompts.MAX_CRITERIA:
                raise QwenLimitError("criteria_count_limit", "추출 조건 수가 128개를 넘습니다.")
        if len(chunks) > 1:
            self._event("cross_chunk_scope_requires_review", "extract_criteria", chunks=len(chunks))
        return {"items": items, "groups": groups}

    def inspect(self, criteria, images, logos=None) -> dict:
        if isinstance(criteria, dict):
            criteria = criteria.get("items")
        if not isinstance(criteria, list) or not criteria or len(criteria) > prompts.MAX_CRITERIA:
            raise QwenInputError("invalid_criteria", "검사에는 1~128개의 실제 조건이 필요합니다.")
        ids = [item.get("id") if isinstance(item, dict) else None for item in criteria]
        if any(not isinstance(key, str) or not key for key in ids) or len(ids) != len(set(ids)):
            raise QwenInputError("invalid_criteria", "검사 조건 ID가 없거나 중복됩니다.")
        from .contracts import Criterion
        try:
            for criterion in criteria:
                Criterion.model_validate(criterion)
        except ValidationError:
            raise QwenInputError("invalid_criteria", "검사 조건이 공용 Criterion 계약에 맞지 않습니다.") from None
        assets = _normalise_images(images, role="asset")
        references = _normalise_images(logos or [], role="logo")
        if not assets:
            raise QwenInputError("missing_asset_images", "검사할 실제 이미지가 필요합니다.")
        if set(item["file_id"] for item in assets).intersection(item["file_id"] for item in references):
            raise QwenInputError("ambiguous_image_ids", "산출물과 기준 로고의 근거 ID는 달라야 합니다.")
        if len({item["frame_ms"] is None for item in assets}) > 1:
            raise QwenInputError("mixed_asset_kinds", "한 번의 검사에 포스터와 영상 프레임을 섞을 수 없습니다.")
        if sum(len(item["data"]) for item in [*assets, *references]) > MAX_IMAGE_TOTAL_BYTES:
            raise QwenInputError("image_total_size_limit", "Qwen 이미지 전체 입력이 16MiB를 넘습니다.")
        creative = [item for item in criteria if item["category"] == "creative" and item.get("supported", True) is True and item["kind"] != "unsupported"]
        observations = [{
            "criterion_id": item["id"], "execution_status": "unsupported", "verdict": "review",
            "observed": "", "expected": item.get("expected"),
            "reason": "이 조건은 자동 검사 범위에 포함되지 않아 검사하지 않았습니다. 사람이 확인해야 합니다.",
            "evidence": [], "limitations": ["not_automatically_inspected"],
        } for item in criteria if item not in creative]
        if not creative:
            self._event("no_supported_creative_criteria", "inspect")
            return {"results": validate_results(criteria, observations, {item["file_id"] for item in [*assets, *references]})}
        batches = [assets[index:index + prompts.MAX_IMAGES_PER_CALL] for index in range(0, len(assets), prompts.MAX_IMAGES_PER_CALL)]
        criterion_batches = [creative[index:index + MAX_CRITERIA_PER_CALL] for index in range(0, len(creative), MAX_CRITERIA_PER_CALL)]
        if len(batches) * len(criterion_batches) > self.max_calls - self.stats["calls"]:
            raise QwenLimitError("qwen_call_limit", "전체 조건·프레임을 검사할 최소 호출 예산이 부족합니다.")
        reference_ids = [item["file_id"] for item in references]
        for batch in batches:
            for criterion_batch in criterion_batches:
                criteria_map = {item["id"]: item for item in criterion_batch}
                registry = {(item["file_id"], item["frame_ms"]): item for item in [*batch, *references]}

                def provenance(parsed):
                    returned = [item["criterion_id"] for item in parsed["results"]]
                    if set(returned) != set(criteria_map) or len(returned) != len(set(returned)):
                        raise ValueError
                    for result in parsed["results"]:
                        if json.dumps(result["expected"], sort_keys=True, allow_nan=False) != json.dumps(criteria_map[result["criterion_id"]].get("expected"), sort_keys=True, allow_nan=False):
                            raise ValueError
                        for evidence in result["evidence"]:
                            if (evidence["file_id"], evidence["frame_ms"]) not in registry:
                                raise ValueError
                            x, y, width, height = evidence["bbox"]
                            if not all(math.isfinite(value) for value in (x, y, width, height)) or min(x, y) < 0 or min(width, height) <= 0 or x + width > 1.000001 or y + height > 1.000001:
                                raise ValueError
                            if registry[(evidence["file_id"], evidence["frame_ms"])]["source_role"] == "asset" and evidence["text"] and evidence["text"] not in result["observed"]:
                                raise ValueError

                try:
                    payload = prompts.build_inspection_request(criterion_batch, batch, references, model=self.model)
                except ValueError:
                    raise QwenInputError("qwen_input_limit", "조건 묶음이 Qwen 입력 한도를 넘습니다.") from None
                inspected = self._structured(payload, prompts.InspectionResponse, "inspect", provenance)
                for result in inspected["results"]:
                    # These fields are computed locally, never accepted from model JSON.
                    result["logo_reference_ids"] = reference_ids
                    result["scope"] = {
                        "asset_kind": "video" if assets[0]["frame_ms"] is not None else "poster",
                        "complete": assets[0]["frame_ms"] is None and (len(assets) == 1 or any(item["role"] == "full" for item in assets)),
                        "all_frames_checked": False,
                        "asset_ids": list(dict.fromkeys(item["asset_id"] for item in batch if "asset_id" in item)),
                        "inspected_frame_ms": [item["frame_ms"] for item in batch],
                    }
                    observations.append(result)
        return {"results": validate_results(criteria, observations, {item["file_id"] for item in [*assets, *references]})}

    def report(self, results) -> dict:
        if isinstance(results, dict):
            results = results.get("results")
        if not isinstance(results, list) or not results or len(results) > prompts.MAX_CRITERIA:
            raise QwenInputError("invalid_report_results", "보고서에는 실제 검증 결과가 필요합니다.")
        by_id = {}
        for item in results:
            if not isinstance(item, dict) or not isinstance(item.get("criterion_id"), str) or item.get("verdict") not in {"pass", "fail", "review"} or item["criterion_id"] in by_id or not isinstance(item.get("reason"), str) or not isinstance(item.get("evidence"), list):
                raise QwenInputError("invalid_report_results", "보고서 결과 ID 또는 판정이 올바르지 않습니다.")
            if any(not isinstance(evidence, dict) or not isinstance(evidence.get("file_id"), str) for evidence in item["evidence"]):
                raise QwenInputError("invalid_report_evidence", "보고서 근거 ID가 올바르지 않습니다.")
            by_id[item["criterion_id"]] = item
        # Do not silently truncate observations to fit the model. Large reports fail explicitly.
        _bounded_json(results, limit=prompts.MAX_INPUT_CHARACTERS - 4000)
        try:
            payload = prompts.build_report_request(results, model=self.model)
        except ValueError:
            raise QwenInputError("qwen_input_limit", "보고서 입력이 Qwen 입력 한도를 넘습니다.") from None

        grounding = prompts.report_grounding(results)

        def provenance(parsed):
            if parsed["summary"] != "\n".join(grounding["grounded_summary_lines"]):
                raise ValueError
            draft = parsed["change_request_draft"]
            if draft["items"] != grounding["allowed_draft_items"] or draft["message"] != grounding["draft_message"]:
                raise ValueError

        return self._structured(payload, prompts.ReportResponse, "report", provenance)
