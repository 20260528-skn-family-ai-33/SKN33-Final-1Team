"""Paddle 단독 API 계약은 가짜 작업자로 검사하고 모델·서버 설치를 하지 않는다."""
import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import api, ocr_runtime


def stub_paddle(monkeypatch, *, ready=True, recognizer=None):
    """단독 모듈을 주입하고 EasyOCR 건강 검사가 호출되면 즉시 실패시킨다."""
    module = SimpleNamespace(paddle_readiness=Mock(return_value=(ready, "private/path")),
                             recognize_paddle=recognizer or Mock())
    monkeypatch.setitem(sys.modules, "backend.app.paddle_ocr", module)
    monkeypatch.setenv("OCR_PIPELINE", "paddle-only")
    monkeypatch.setenv("RUN_TARGET", "local")
    easy_status = Mock(side_effect=AssertionError("Paddle must not inspect EasyOCR"))
    monkeypatch.setattr(ocr_runtime, "get_runtime_status", easy_status)
    monkeypatch.setattr(ocr_runtime, "recognize_image", easy_status)
    return module, easy_status


@pytest.mark.parametrize("ready", [True, False])
@pytest.mark.parametrize("explicit", [True, False])
def test_paddle_health_uses_only_selected_runtime(tmp_path, monkeypatch, ready, explicit):
    """Paddle 준비 여부와 무관하게 EasyOCR 없는 상태에서 건강 검사가 동작한다."""
    module, easy_status = stub_paddle(monkeypatch, ready=ready)
    if not explicit:
        monkeypatch.delenv("OCR_PIPELINE")
    with TestClient(api.create_app(review_root=tmp_path)) as client:
        response = client.get("/api/health").json()
    assert response["runtime"]["engine"] == "paddleocr"
    assert response["runtime"]["pipeline"] == "paddle_full_v1"
    assert response["runtime"]["ready"] is ready
    assert "private/path" not in json.dumps(response)
    module.paddle_readiness.assert_called_once_with()
    easy_status.assert_not_called()


@pytest.mark.parametrize("explicit", [True, False])
def test_paddle_recognizer_dispatch_has_no_baseline_call(tmp_path, monkeypatch, explicit):
    """단독 선택을 EasyOCR 기준선 호출보다 먼저 분기한다."""
    module, easy_status = stub_paddle(monkeypatch)
    if not explicit:
        monkeypatch.delenv("OCR_PIPELINE")
    module.recognize_paddle.return_value = {"records": []}
    image = tmp_path / "input.png"
    assert api._default_recognizer(image) == {"records": []}
    module.recognize_paddle.assert_called_once_with(image, tmp_path / "paddle-only")
    easy_status.assert_not_called()


@pytest.mark.parametrize("target", ["local", "runpod"])
def test_baseline_override_and_runpod_default_keep_easyocr_adapter(tmp_path, monkeypatch, target):
    """명시한 EasyOCR 비교 경로와 RunPod 기본값이 Paddle로 바뀌지 않게 한다."""
    module, easy_status = stub_paddle(monkeypatch)
    monkeypatch.setenv("RUN_TARGET", target)
    if target == "local":
        monkeypatch.setenv("OCR_PIPELINE", "baseline")
    else:
        monkeypatch.delenv("OCR_PIPELINE")
    easy_status.side_effect = None
    easy_status.return_value = {"engine": "easyocr", "execution_target": target, "ready": True}
    assert api._default_runtime_status()["engine"] == "easyocr"
    assert api._default_recognizer(tmp_path / "input.png")["engine"] == "easyocr"
    assert api._runtime_identity() == {"engine": "easyocr", "execution_target": target}
    module.paddle_readiness.assert_not_called()
    module.recognize_paddle.assert_not_called()


@pytest.mark.parametrize("pipeline", ["paddle-only", "paddle", "qwen", "adaptive"])
def test_local_only_health_rejects_runpod_without_inspecting_models(monkeypatch, pipeline):
    """지원하지 않는 실행 조합은 모델 점검 전에 준비 부족으로 반환한다."""
    module, easy_status = stub_paddle(monkeypatch)
    monkeypatch.setenv("OCR_PIPELINE", pipeline)
    monkeypatch.setenv("RUN_TARGET", "runpod")
    assert api._default_runtime_status()["ready"] is False
    module.paddle_readiness.assert_not_called()
    easy_status.assert_not_called()


def submit(client):
    """실제 업로드 API를 통해 좌표 복원과 판정의 연결을 검증한다."""
    output = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, "PNG")
    return client.post("/api/reviews", data={"criteria_version": "1", "project_name": "Paddle 계약",
        "criteria": json.dumps([{"id": "place", "text": "장소: 직지문화공원", "type": "information", "source": "user"}])},
        files={"image": ("poster.png", output.getvalue(), "image/png")})


def test_paddle_full_result_preserves_engine_polygon_and_multiline(tmp_path, monkeypatch):
    """단독 결과도 공개 좌표를 한 번 복원하고 기존 여러 줄 추출기에 전달한다."""
    record = {"id": "p1", "text": "직지문화공원", "confidence": .96, "recognition_engine": "paddleocr",
              "bbox_xyxy": [20, 40, 180, 80], "polygon": [[20, 40], [180, 40], [180, 80], [20, 80]]}
    payload = {"engine": "paddleocr", "pipeline": "paddle_full_v1", "execution_target": "local",
               "device": "cpu", "status": "success", "records": [record], "input_dimensions": [200, 200],
               "private_model_dir": "private/model/path"}
    stub_paddle(monkeypatch, recognizer=Mock(return_value=payload))
    with TestClient(api.create_app(review_root=tmp_path)) as client:
        response = submit(client)
        result = client.get(f"/api/reviews/{response.json()['id']}").json()
    assert result["status"] == "completed"
    assert result["runtime"]["engine"] == "paddleocr"
    assert result["runtime"]["pipeline"] == "paddle_full_v1"
    assert result["results"][0]["status"] == "pass"
    assert result["ocr"]["records"][0]["bbox_xyxy"] == [10, 20, 90, 40]
    assert result["ocr"]["records"][0]["polygon"] == [[10, 20], [90, 20], [90, 40], [10, 40]]
    assert result["ocr"]["records"][0]["recognition_engine"] == "paddleocr"
    assert "event" in result["ocr"]
    assert "private/model/path" not in json.dumps(result)
    assert (tmp_path / response.json()["id"] / "ocr.raw.json").is_file()


def test_paddle_failure_keeps_selected_engine_even_without_attempts(tmp_path, monkeypatch):
    """준비·작업자 오류에도 잘못된 EasyOCR 엔진을 표시하지 않는다."""
    stub_paddle(monkeypatch, recognizer=Mock(side_effect=RuntimeError("private/path/token")))
    with TestClient(api.create_app(review_root=tmp_path)) as client:
        response = submit(client)
        result = client.get(f"/api/reviews/{response.json()['id']}").json()
    assert result["status"] == "failed" and result["results"] == []
    assert result["runtime"]["engine"] == "paddleocr"
    assert result["runtime"]["pipeline"] == "paddle_full_v1"
    assert "private/path/token" not in json.dumps(result)


def test_baseline_evidence_metadata_is_validated_and_preserved():
    """혼합 경로의 원문 근거가 공개 좌표로 전달되어 항목별 판정에 쓰이게 한다."""
    record = {"id": "p1", "text": "직지문화공원, 친환경생태공원", "confidence": .96,
              "bbox_xyxy": [20, 40, 180, 80], "requires_review": True, "recognition_engine": "paddleocr",
              "baseline_text": "직지문화공원 친환경생대공원", "baseline_confidence": .95,
              "baseline_recognition_engine": "easyocr", "baseline_requires_review": False,
              "selection_reason": "new_model_candidate_unverified", "secret": "not exposed"}
    mapped = api._map_records({"records": [record]}, 100, 100)[0]
    for field in ("baseline_text", "baseline_confidence", "baseline_recognition_engine", "baseline_requires_review", "selection_reason"):
        assert mapped[field] == record[field]
    assert "secret" not in mapped


@pytest.mark.parametrize("field,value", [("baseline_confidence", float("nan")), ("baseline_confidence", True),
    ("baseline_confidence", 1.1), ("baseline_requires_review", "false"), ("baseline_recognition_engine", "private/path")])
def test_invalid_baseline_metadata_cannot_change_verdict(field, value):
    """잘못된 원문 점수·출처·표시를 자동 충족 근거로 받아들이지 않는다."""
    record = {"id": "p1", "text": "직지문화공원", "confidence": .96, "bbox_xyxy": [20, 40, 180, 80], field: value}
    with pytest.raises(ValueError):
        api._map_records({"records": [record]}, 100, 100)


def test_paddle_payload_supplies_record_engine_without_lowering_threshold():
    """레코드 출처 누락이 Paddle 점수를 EasyOCR 기준으로 바꾸지 않도록 한다."""
    record = {"id": "p1", "text": "직지문화공원", "confidence": .8, "bbox_xyxy": [20, 40, 180, 80]}
    mapped = api._map_records({"engine": "paddleocr", "records": [record]}, 100, 100)
    assert mapped[0]["recognition_engine"] == "paddleocr"
    result = api.review_criteria([{"id": "place", "text": "장소: 직지문화공원", "type": "information"}], mapped)
    assert result[0]["status"] == "review"


@pytest.mark.parametrize("engine", ["unknown", None, 42, ["paddleocr"]])
def test_explicit_unknown_record_engine_is_rejected(engine):
    """알 수 없는 명시적 출처를 삭제해 기본 엔진으로 판정하는 회귀를 막는다."""
    record = {"id": "p1", "text": "직지문화공원", "confidence": .95,
              "bbox_xyxy": [20, 40, 180, 80], "recognition_engine": engine}
    with pytest.raises(ValueError):
        api._map_records({"records": [record]}, 100, 100)


@pytest.mark.parametrize("identity", [{"engine": "paddleocr"}, {"pipeline": "paddle_full_v1"}])
def test_paddle_payload_cannot_claim_easyocr_record_scores(identity):
    """단독 엔진과 레코드 출처 불일치로 더 낮은 점수 기준을 적용하지 않는다."""
    record = {"id": "p1", "text": "직지문화공원", "confidence": .8,
              "bbox_xyxy": [20, 40, 180, 80], "recognition_engine": "easyocr"}
    with pytest.raises(ValueError):
        api._map_records({**identity, "records": [record]}, 100, 100)
