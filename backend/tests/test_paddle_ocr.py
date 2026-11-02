"""Paddle 단독 실행의 좌표·무결성·실패 계약을 다운로드 없이 검증한다."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest

from backend.app import paddle_ocr as runtime
from backend.app import paddle_full_worker as worker
from scripts import prepare_paddle as provision


def raw_result():
    """입력 픽셀 좌표에 기울어진 사각형을 가진 실제 Paddle 응답 구조를 만든다."""
    return {"res": {"rec_texts": ["직지문화공원"], "rec_scores": [.95],
                    "rec_polys": [[[10, 15], [180, 10], [190, 40], [20, 45]]]}}


def fake_identity(directory, model):
    """실제 모델 파일을 읽지 않고 식별자 일치 여부를 검사할 값을 반환한다."""
    return {"model": model}


def install_worker_mock(monkeypatch, tmp_path, *, failure=None, mutate=None):
    """격리 작업자 실행만 대체하고 파일 출력·응답 검증 경로는 그대로 둔다."""
    monkeypatch.setattr(runtime, "paddle_readiness", lambda **kwargs: (True, "ready"))
    monkeypatch.setattr(runtime, "paddle_paths", lambda: (tmp_path / "python", tmp_path / "det", tmp_path / "rec"))
    monkeypatch.setattr(runtime, "verify_model", fake_identity)

    def run(command, **kwargs):
        """작업자 명령에서 지정된 파일에 한 이미지의 원시 응답을 기록한다."""
        assert command[2] == "backend.app.paddle_full_worker"
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        if failure is not None:
            raise failure
        payload = {"raw": raw_result(), "device": "cpu", "input_dimensions": [200, 100],
                   "model_identity": {"detection": {"model": provision.DETECTION_MODEL},
                                      "recognition": {"model": provision.MODEL}},
                   "load_seconds": .2, "inference_seconds": .1}
        if mutate:
            mutate(payload)
        Path(command[command.index("--output") + 1]).write_text(json.dumps(payload), encoding="utf-8")

    mock = Mock(side_effect=run)
    monkeypatch.setattr(runtime.subprocess, "run", mock)
    source = tmp_path / "input.png"
    Image.new("RGB", (200, 100)).save(source)
    return source, mock


def test_paddle_only_preserves_polygon_and_does_not_force_review(monkeypatch, tmp_path):
    """EasyOCR를 import할 수 없어도 단독 경로가 좌표·엔진·원시 근거를 보존한다."""
    monkeypatch.setitem(sys.modules, "easyocr", None)
    source, call = install_worker_mock(monkeypatch, tmp_path)
    result = runtime.recognize_paddle(source, tmp_path / "attempt", timeout=240)
    assert result["engine"] == "paddleocr" and result["pipeline"] == "paddle_full_v1"
    assert result["records"][0]["bbox_xyxy"] == [10, 10, 190, 45]
    assert result["records"][0]["polygon"] == raw_result()["res"]["rec_polys"][0]
    assert "requires_review" not in result["records"][0]
    assert result["records"][0]["recognition_engine"] == "paddleocr"
    assert result["raw"] == raw_result()
    assert call.call_count == 1 and call.call_args.kwargs["timeout"] == 120
    assert (tmp_path / "attempt/paddle.full.raw.json").is_file()
    with pytest.raises(FileExistsError):
        runtime.recognize_paddle(source, tmp_path / "attempt")


@pytest.mark.parametrize("field,value", [
    ("rec_scores", []), ("rec_scores", [float("nan")]), ("rec_scores", [True]),
    ("rec_texts", [None]), ("rec_polys", [[[0, 0], [200, 0], [201, 20], [0, 20]]]),
    ("rec_polys", [[[0, 0], [0, 0], [0, 0], [0, 0]]]),
    ("rec_polys", [[[0, 0], [200, 0], [200, float("inf")], [0, 20]]]),
])
def test_invalid_parallel_arrays_or_coordinates_are_rejected(field, value):
    """잘못된 응답을 zip으로 조용히 잘라내거나 유효한 화면 근거로 바꾸지 않는다."""
    raw = raw_result()
    raw["res"][field] = value
    with pytest.raises(ValueError):
        runtime.normalize_records(raw, 200, 100)


def test_empty_detection_is_a_valid_result():
    """글자가 없는 이미지의 빈 결과를 실행 오류나 허구의 문구로 바꾸지 않는다."""
    assert runtime.normalize_records({"rec_texts": [], "rec_scores": [], "rec_polys": []}, 200, 100) == []


@pytest.mark.parametrize("failure,code", [(subprocess.TimeoutExpired("worker", 1), "timeout"),
                                        (subprocess.CalledProcessError(1, "worker"), "paddle_failed")])
def test_worker_failure_never_falls_back_to_easyocr(monkeypatch, tmp_path, failure, code):
    """시간 초과·작업자 실패는 실패 근거를 남기며 다른 모델로 재시도하지 않는다."""
    source, call = install_worker_mock(monkeypatch, tmp_path, failure=failure)
    with pytest.raises(runtime.OCRRuntimeError) as caught:
        runtime.recognize_paddle(source, tmp_path / "attempt", timeout=3)
    assert caught.value.code == code and caught.value.attempts[0]["status"] == "failed"
    assert call.call_count == 1 and call.call_args.kwargs["timeout"] == 3
    assert not (tmp_path / "attempt/result.json").exists()


def test_dimension_mismatch_fails_without_returning_unmapped_coordinates(monkeypatch, tmp_path):
    """다른 크기의 작업자 결과를 원본 좌표로 잘못 공개하지 않는다."""
    source, _ = install_worker_mock(monkeypatch, tmp_path, mutate=lambda value: value.update(input_dimensions=[100, 50]))
    with pytest.raises(runtime.OCRRuntimeError):
        runtime.recognize_paddle(source, tmp_path / "attempt")
    assert (tmp_path / "attempt/paddle.full.raw.json").is_file()


def test_missing_model_stops_before_worker_and_download(monkeypatch, tmp_path):
    """준비되지 않은 모델은 작업자·다운로드 호출 없이 실행을 중단한다."""
    monkeypatch.setattr(runtime, "paddle_readiness", lambda **kwargs: (False, "missing"))
    run, download = Mock(), Mock()
    monkeypatch.setattr(runtime.subprocess, "run", run)
    monkeypatch.setattr(provision.urllib.request, "urlopen", download)
    with pytest.raises(runtime.OCRRuntimeError) as caught:
        runtime.recognize_paddle(tmp_path / "missing.png", tmp_path / "attempt")
    assert caught.value.code == "model_not_ready"
    run.assert_not_called()
    download.assert_not_called()
    assert not (tmp_path / "attempt").exists()


def test_runpod_is_rejected_without_probing_packages(monkeypatch):
    """지원하지 않는 원격 대상을 로컬 실행이나 네트워크 호출로 바꾸지 않는다."""
    monkeypatch.setenv("RUN_TARGET", "runpod")
    run = Mock()
    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime.paddle_readiness(check_import=True)[0] is False
    run.assert_not_called()


def test_pinned_model_identity_and_bytes_are_both_checked(monkeypatch, tmp_path):
    """파일과 manifest를 같이 바꿔도 코드에 고정한 공식 해시를 통과하지 못한다."""
    name, content = "inference.json", b"model bytes"
    spec = {"revision": "fixed", "sha256": {name: hashlib.sha256(content).hexdigest()}}
    monkeypatch.setitem(provision.MODEL_SPECS, provision.MODEL, spec)
    manifest = {"model": provision.MODEL, "repository": f"PaddlePaddle/{provision.MODEL}", **spec}
    (tmp_path / name).write_bytes(content)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    assert provision.verify_model(tmp_path, provision.MODEL) == manifest
    (tmp_path / name).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        provision.verify_model(tmp_path, provision.MODEL)
    altered = copy.deepcopy(manifest)
    altered["sha256"][name] = hashlib.sha256(b"tampered").hexdigest()
    path.write_text(json.dumps(altered))
    with pytest.raises(ValueError):
        provision.verify_model(tmp_path, provision.MODEL)


def test_prepare_defaults_to_recognition_and_detection_is_explicit(monkeypatch):
    """기존 준비 명령은 유지하고 명시 옵션일 때만 탐지 모델을 추가한다."""
    call = Mock()
    monkeypatch.setattr(provision, "prepare_model", call)
    provision.prepare()
    call.assert_called_once_with(provision.MODEL)
    call.reset_mock()
    provision.prepare(include_detection=True)
    assert [entry.args[0] for entry in call.call_args_list] == [provision.MODEL, provision.DETECTION_MODEL]


def test_english_retry_model_is_only_prepared_explicitly(monkeypatch):
    call=Mock()
    monkeypatch.setattr(provision,'prepare_model',call)
    provision.prepare(include_english=True)
    assert [entry.args[0] for entry in call.call_args_list]==[provision.MODEL,provision.ENGLISH_MODEL]


@pytest.mark.parametrize("filename", ("input.png", "10000000-0000-4000-8000-000000000001"))
def test_full_worker_uses_only_explicit_local_models_and_disables_transforms(monkeypatch, tmp_path, filename):
    """추가 자동 다운로드가 생기는 문서 보정·회전 모델을 모두 끈다."""
    monkeypatch.setattr(worker, "verify_model", fake_identity)
    monkeypatch.setattr(worker.importlib.metadata, "version", lambda name: "test")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(tmp_path))
    predictor = Mock(return_value=[SimpleNamespace(json=raw_result())])
    model = Mock(return_value=SimpleNamespace(predict=predictor))
    monkeypatch.setitem(sys.modules, "paddle", SimpleNamespace(get_device=lambda: "cpu", __version__="test"))
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=model))
    monkeypatch.setitem(sys.modules, "easyocr", None)
    source = tmp_path / filename
    Image.new("RGB", (200, 100), (10, 20, 30)).save(source, format="PNG")
    worker.run(source, tmp_path / "raw.json", tmp_path / "det", tmp_path / "rec")
    pixels = predictor.call_args.kwargs["input"]
    assert pixels.shape == (100, 200, 3) and pixels[0, 0].tolist() == [30, 20, 10]
    assert pixels.flags.c_contiguous
    args = model.call_args.kwargs
    for option in ("use_doc_orientation_classify", "use_doc_unwarping", "use_textline_orientation"):
        assert args[option] is False
    assert args["text_detection_model_name"] == provision.DETECTION_MODEL
    assert args["text_recognition_model_name"] == provision.MODEL
    assert args["text_detection_model_dir"] == str(tmp_path / "det")
    assert args["text_recognition_model_dir"] == str(tmp_path / "rec")
    assert args["text_rec_score_thresh"] == 0.0
    assert json.loads((tmp_path / "raw.json").read_text())["raw"] == raw_result()


def test_readiness_only_imports_when_requested_and_reports_import_failure(monkeypatch, tmp_path):
    """파일 점검에서 추론하지 않고 명시한 import 검사 오류를 준비 부족으로 알린다."""
    python = tmp_path / "python"
    python.touch()
    monkeypatch.setenv("RUN_TARGET", "local")
    monkeypatch.setattr(runtime, "paddle_paths", lambda: (python, tmp_path / "det", tmp_path / "rec"))
    monkeypatch.setattr(runtime, "verify_model", fake_identity)
    call = Mock(side_effect=subprocess.CalledProcessError(1, "python"))
    monkeypatch.setattr(runtime.subprocess, "run", call)
    assert runtime.paddle_readiness()[0]
    call.assert_not_called()
    ready, detail = runtime.paddle_readiness(check_import=True)
    assert not ready and "불러오기" in detail
    assert call.call_args.args[0][1:] == ["-c", "import paddle; from paddleocr import PaddleOCR"]


def test_readiness_hash_failure_never_runs_worker(monkeypatch, tmp_path):
    """로컬 파일이 변조되면 import·서버 실행보다 먼저 준비 부족을 알린다."""
    python = tmp_path / "python"
    python.touch()
    monkeypatch.setenv("RUN_TARGET", "local")
    monkeypatch.setattr(runtime, "paddle_paths", lambda: (python, tmp_path / "det", tmp_path / "rec"))
    monkeypatch.setattr(runtime, "verify_model", Mock(side_effect=ValueError("hash mismatch")))
    call = Mock()
    monkeypatch.setattr(runtime.subprocess, "run", call)
    assert runtime.paddle_readiness(check_import=True)[0] is False
    call.assert_not_called()


def test_prepare_keeps_valid_existing_cache_without_network(monkeypatch, tmp_path):
    """명시적인 준비 명령을 재실행해도 유효한 캐시는 다시 다운로드하지 않는다."""
    monkeypatch.setattr(provision, "ROOT", tmp_path)
    name, content = "inference.json", b"model bytes"
    spec = {"revision": "fixed", "sha256": {name: hashlib.sha256(content).hexdigest()}}
    monkeypatch.setitem(provision.MODEL_SPECS, provision.MODEL, spec)
    directory = tmp_path / ".runtime/paddle-models" / provision.MODEL
    directory.mkdir(parents=True)
    (directory / name).write_bytes(content)
    download = Mock()
    monkeypatch.setattr(provision.urllib.request, "urlopen", download)
    provision.prepare_model(provision.MODEL)
    download.assert_not_called()
    assert provision.verify_model(directory, provision.MODEL)["revision"] == "fixed"


@pytest.mark.parametrize("timeout", [0, -1, 241, float("nan"), float("inf"), True, "120"])
def test_invalid_timeout_is_rejected_before_any_runtime_work(monkeypatch, tmp_path, timeout):
    """비정상 제한 시간으로 무제한 작업자가 시작되지 않게 진입점에서 거절한다."""
    readiness = Mock()
    monkeypatch.setattr(runtime, "paddle_readiness", readiness)
    with pytest.raises(ValueError):
        runtime.recognize_paddle(tmp_path / "input.png", tmp_path / "attempt", timeout=timeout)
    readiness.assert_not_called()
