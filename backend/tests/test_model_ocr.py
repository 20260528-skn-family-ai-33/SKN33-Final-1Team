"""추가 OCR 모델의 격리·근거·미확정 판정 계약을 모델 없이 검증한다."""
import copy
import json
import subprocess
from unittest.mock import Mock

from PIL import Image
import pytest

from backend.app import model_ocr as models
from backend.app.api import _map_records, _safe_runtime, _default_recognizer
from backend.app.review_rules import review_criteria


def record(text="친환경생대공원", confidence=.99):
    """동일 위치의 다른 글자 후보를 비교할 수 있는 근거를 만든다."""
    return {"id": "r1", "text": text, "confidence": confidence, "bbox_xyxy": [10,10,150,40],
            "polygon": [[10,10],[150,10],[150,40],[10,40]]}


@pytest.mark.parametrize("engine,score", [("paddleocr", .99), ("qwen3-vl", None)])
def test_new_model_score_never_grants_automatic_pass(engine, score):
    """모델마다 다른 점수 체계를 통과 기준에 그대로 사용하지 않는다."""
    original = record()
    candidate = models.model_candidate(original, "친환경생태공원", score, engine, "one")
    assert original["text"] == "친환경생대공원" and "alternatives" not in original
    assert candidate["text"] == "친환경생태공원" and candidate["confidence"] == score
    assert candidate["baseline_text"] == original["text"]
    assert candidate["baseline_confidence"] == .99
    assert candidate["baseline_recognition_engine"] == "easyocr"
    assert candidate["baseline_requires_review"] is False
    verdict = review_criteria([{"id":"p", "text":"장소: 친환경생태공원", "type":"information"}], [candidate], multiline=True)
    assert verdict[0]["status"] == "review"
    assert candidate["requires_review"]


def test_agreement_and_abstention_do_not_confirm_text():
    """동일 문구 또는 null 응답도 검증된 정답으로 취급하지 않는다."""
    for text in (None, "", record()["text"]):
        result = models.model_candidate(record(), text, None, "qwen3-vl", "r1")
        assert result["requires_review"] and result["text"] == record()["text"]


@pytest.mark.parametrize("url", ["https://127.0.0.1:11435", "http://example.com", "http://localhost:11435",
                                   "http://127.0.0.1.evil.test", "http://user:secret@127.0.0.1", "http://127.0.0.1/path"])
def test_images_cannot_be_sent_to_remote_or_credential_urls(monkeypatch, url):
    """로컬 전용 경로에서 임의 외부 주소와 자격증명 주소를 차단한다."""
    monkeypatch.setenv("OCR_OLLAMA_URL", url)
    with pytest.raises(models.OCRRuntimeError):
        models.ollama_url()


def test_qwen_prompt_uses_pixels_only_and_preserves_raw_invalid_response(monkeypatch, tmp_path):
    """요청에 기준·파일명·정답 없이 픽셀을 전달하고 잘못된 원시 응답도 남긴다."""
    response = {"model":"qwen3-vl:4b-instruct", "done":True, "done_reason":"stop", "message":{"content":"not json"}}
    request = Mock(return_value=response)
    monkeypatch.setattr(models, "ollama_request", request)
    target = tmp_path / "raw.json"
    with pytest.raises(ValueError):
        models.qwen_read(Image.new("RGB", (30,20)), full=False, timeout=99, raw_path=target)
    payload = request.call_args.args[1]
    assert payload["model"] == "qwen3-vl:4b-instruct" and payload["messages"][0]["images"]
    assert payload["options"]["num_predict"] == 512 and request.call_args.kwargs["timeout"] == 60
    assert payload["messages"][0]["content"] == models.CROP_PROMPT
    assert json.loads(target.read_text()) == response


@pytest.mark.parametrize("content", [{"lines":[{"text":"x","box":[0,0,1001,10]}]},
                                      {"lines":[{"text":"x","box":[10,0,5,10]}]},
                                      {"lines":[{"text":"x","box":[0,0,float('nan'),10]}]}, {"lines":[{}]}])
def test_full_image_invalid_coordinates_rejected(content):
    """범위 밖·역전·비정상 수치의 VLM 위치는 근거로 공개하지 않는다."""
    with pytest.raises(ValueError):
        models.full_records(content, 200, 100)


def test_full_image_coordinates_scale_once_and_keep_no_confidence():
    """0..1000 VLM 좌표를 입력 픽셀과 화면 픽셀로 각각 한 번 복원한다."""
    raw = models.full_records({"lines":[{"text":"봄 축제", "box":[100,200,800,400]}]}, 400,200)
    mapped = _map_records({"records":raw,"input_dimensions":[400,200]}, 200,100)
    assert mapped[0]["bbox_xyxy"] == [20,20,160,40]
    assert mapped[0]["confidence"] is None and mapped[0]["requires_review"]


def test_full_image_size_is_bounded_and_transform_is_saved(tmp_path):
    """큰 전체 이미지의 추론 크기를 제한하되 원본 좌표 복원을 유지한다."""
    image=models.prepare_full_image(Image.new("RGB",(3000,2000)),tmp_path)
    assert image.size==(1536,1024)
    transform=json.loads((tmp_path/"full-transform.json").read_text())
    assert transform["full_to_input_matrix"][0][0]==3000/1536
    assert transform["input_dimensions"]==[3000,2000]


def fake_baseline(image_path, directory, timeout):
    """새 모델 실패 시 기준선 보존 동작만 확인할 독립 결과를 반환한다."""
    return {"status":"success", "raw":{"engine":"easyocr","execution_target":"local", "status":"success",
             "device":"mps", "input_dimensions":[200,100], "records":[record()]}, "inference_seconds":.1}


def test_paddle_timeout_preserves_baseline_and_marks_partial(monkeypatch, tmp_path):
    """새 모델 시간 초과를 성공적인 재인식으로 기록하지 않는다."""
    monkeypatch.setattr(models, "model_readiness", lambda *a, **k:(True,"ready"))
    monkeypatch.setattr(models, "baseline_once", fake_baseline)
    monkeypatch.setattr(models, "paddle_read", Mock(side_effect=subprocess.TimeoutExpired("worker",1)))
    path = tmp_path / "input.png"
    Image.new("RGB", (200,100)).save(path)
    result = models.recognize_models(path,tmp_path/"attempt","paddle",timeout=5)
    assert result["refinement_status"] == "partial" and result["records"][0]["requires_review"]
    assert result["records"][0]["selection_reason"] == "refinement_incomplete"
    assert result["records"][0]["text"] == result["baseline_records"][0]["text"]
    assert result["refinements"][-1]["error_type"] == "TimeoutExpired"
    with pytest.raises(FileExistsError):
        models.recognize_models(path,tmp_path/"attempt","paddle",timeout=5)


def test_missing_model_never_calls_inference_or_download(monkeypatch, tmp_path):
    """준비되지 않은 모델은 기준선 실행 전 명확하게 중단한다."""
    baseline = Mock()
    monkeypatch.setattr(models,"model_readiness",lambda *a, **k:(False,"missing"))
    monkeypatch.setattr(models,"baseline_once",baseline)
    with pytest.raises(models.OCRRuntimeError):
        models.recognize_models(tmp_path/"input.png",tmp_path/"attempt","qwen")
    baseline.assert_not_called()
    assert not (tmp_path/"attempt").exists()


def test_public_engine_and_candidate_provenance_survive_mapping():
    """혼합 모델을 EasyOCR로 오기록하지 않고 비공개 설정은 응답에서 제외한다."""
    runtime = _safe_runtime({"engine":"easyocr+qwen3-vl", "device":"gpu", "pipeline":"qwen_regions_v1", "secret":"hidden"})
    assert runtime["engine"] == "easyocr+qwen3-vl" and "secret" not in runtime
    source = models.model_candidate(record(), "친환경생태공원", None, "qwen3-vl", "one")
    result = _map_records({"records":[source]},100,50)[0]
    assert result["alternatives"][0]["engine"] == "qwen3-vl"
    assert result["alternatives"][0]["bbox_xyxy"] == [5,5,75,20]


@pytest.mark.parametrize("pipeline", ["paddle","qwen"])
def test_api_dispatches_selected_model(monkeypatch, tmp_path, pipeline):
    """운영 API에서 선택한 새 모델 어댑터를 실행하는지 확인한다."""
    mock = Mock(return_value={"engine":models.ENGINES[pipeline]})
    monkeypatch.setenv("OCR_PIPELINE",pipeline)
    monkeypatch.setattr(models,"recognize_models",mock)
    source = tmp_path/"input.png"
    assert _default_recognizer(source)["engine"] == models.ENGINES[pipeline]
    mock.assert_called_once_with(source,tmp_path/pipeline,pipeline)


def test_qwen_timeout_stops_followup_requests(monkeypatch,tmp_path):
    """응답 시간 초과 후 전체 이미지나 다음 영역으로 무제한 재시도하지 않는다."""
    monkeypatch.setattr(models,"model_readiness",lambda *a, **k:(True,"ready"))
    monkeypatch.setattr(models,"baseline_once",fake_baseline)
    monkeypatch.setattr(models,"ollama_request",lambda route: {"models":[{"name":"qwen3-vl:4b-instruct","digest":"local"}]} if route=="/api/tags" else {"version":"test"})
    request = Mock(side_effect=TimeoutError("request timeout"))
    monkeypatch.setattr(models,"qwen_read",request)
    path=tmp_path/"input.png"
    Image.new("RGB",(200,100)).save(path)
    result=models.recognize_models(path,tmp_path/"attempt","qwen")
    assert request.call_count==1 and result["refinement_status"]=="partial"
    assert result["records"][0]["requires_review"]


def test_model_pipeline_refuses_runpod_without_network(monkeypatch):
    """새 로컬 모델의 실패를 RunPod 호출로 전환하지 않는다."""
    monkeypatch.setenv("RUN_TARGET","runpod")
    request=Mock()
    monkeypatch.setattr(models,"ollama_request",request)
    assert models.model_readiness("qwen")[0] is False
    request.assert_not_called()


def test_api_saves_new_model_raw_and_shows_review_evidence(tmp_path):
    """새 모델 결과의 완료 상태와 보류 판정·원문·좌표를 실제 API 흐름에서 확인한다."""
    from backend.tests.test_review_api import make_client, submit, criterion

    def recognize(path):
        """모델 호출 없이 로컬 Qwen 응답 형식을 재현한다."""
        source = models.model_candidate(record(),"친환경생태공원",None,"qwen3-vl","one")
        return {"status":"success","engine":"easyocr+qwen3-vl","execution_target":"local","device":"gpu",
                "pipeline":"qwen_regions_v1","refinement_status":"completed","input_dimensions":[200,300],
                "records":[source], "model_identity":{"private":"not public"}}

    with make_client(tmp_path, recognize) as client:
        identifier=submit(client,criteria=[criterion("장소: 친환경생태공원")]).json()["id"]
        result=client.get(f"/api/reviews/{identifier}").json()
    assert result["status"]=="completed" and result["results"][0]["status"]=="review"
    evidence=result["results"][0]["evidence"][0]
    assert evidence["baseline_text"]=="친환경생대공원"
    assert evidence["alternatives"][0]["engine"]=="qwen3-vl"
    assert evidence["bbox_xyxy"]==[5,5,75,20]
    assert (tmp_path/identifier/"ocr.raw.json").is_file()
    assert "not public" not in json.dumps(result)
