"""API tests use injected OCR evidence, never load/download an OCR model."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import api as backend
from backend.app.review_rules import review_criteria


def image_bytes(mode="RGB", size=(100, 150), color="blue", image_format="PNG", exif=None):
    output = io.BytesIO()
    options = {"exif": exif} if exif else {}
    Image.new(mode, size, color).save(output, image_format, **options)
    return output.getvalue()


def criterion(text="행사명: 김천김밥축제", kind="information", item_id="title"):
    return {"id": item_id, "text": text, "type": kind, "source": "user"}


def record(text, item_id="r1", confidence=.95, box=None):
    return {"id": item_id, "text": text, "confidence": confidence, "bbox_xyxy": box or [20, 40, 180, 70]}


def runtime(records=None):
    def recognize(path):
        with Image.open(path) as image:
            assert image.mode == "RGB"
            dimensions = list(image.size)
        return {
            "records": records if records is not None else [record("김천김밥축제")],
            "status": "success", "engine": "easyocr", "execution_target": "local", "device": "cpu",
            "attempts": [{"device": "mps", "status": "fallback", "reason": "not exported"}, {"device": "cpu", "status": "success"}],
            "input_dimensions": dimensions,
        }
    return recognize


def make_client(tmp_path, recognizer=None):
    app = backend.create_app(
        review_root=tmp_path,
        recognizer=recognizer or runtime(),
        runtime_status=lambda: {"engine": "easyocr", "execution_target": "local", "device": "cpu", "ready": True, "detail": "secret/path"},
    )
    return TestClient(app)


def submit(client, *, criteria=None, version="1", image=None, filename="poster.jpg"):
    return client.post(
        "/api/reviews",
        data={"criteria": json.dumps(criteria if criteria is not None else [criterion()]), "criteria_version": version, "project_name": "샘플 프로젝트"},
        files={"image": (filename, image if image is not None else image_bytes(), "image/jpeg")},
    )


def test_upload_freezes_criteria_preserves_bytes_and_maps_coordinates(tmp_path):
    criteria = [criterion(), criterion("로고가 보여야 합니다.", "visual", "logo")]
    original = image_bytes()
    with make_client(tmp_path) as client:
        response = submit(client, criteria=criteria, version="3", image=original)
        assert response.status_code == 202
        identifier = response.json()["id"]
        result = client.get(f"/api/reviews/{identifier}").json()
        assert result["status"] == "completed"
        assert result["stage"] == "completed" and result["progress"] == 100
        assert result["criteria_version"] == 3
        assert result["criteria"] == criteria
        assert [item["criterion_id"] for item in result["results"]] == ["title", "logo"]
        assert [item["status"] for item in result["results"]] == ["pass", "review"]
        assert result["results"][0]["evidence"][0]["bbox_xyxy"] == [10, 20, 90, 35]
        assert result["image"]["actual_format"] == "PNG"  # misleading extension is permitted
        assert result["image"]["sha256"] == hashlib.sha256(original).hexdigest()
        assert (tmp_path / identifier / "original.bin").read_bytes() == original
        snapshot = json.loads((tmp_path / identifier / "snapshot.json").read_text(encoding="utf-8"))
        assert snapshot["criteria"] == criteria and snapshot["criteria_version"] == 3
        assert snapshot["image_source"]["original_bytes"] == len(original)
        assert client.put(f"/api/reviews/{identifier}", json={"criteria_version": 9}).status_code == 405
        assert client.get(result["image"]["url"]).headers["content-type"] == "image/png"
        assert client.get(f"/{identifier}/original.bin").status_code == 404
        assert client.get(f"/api/reviews/{identifier}/snapshot.json").status_code == 404


def test_pdf_expectations_survive_snapshot_and_metadata_checks_need_no_remote_model(tmp_path, monkeypatch):
    from backend.app.document_conditions import render_condition
    monkeypatch.delenv('REVIEW_MODELS_URL', raising=False)
    spec = {'label':'파일 형식과 색상','requirement':'정적 JPEG 이미지 1개, 확장자 .jpg, RGB 색상 모드',
            'level':'필수','inspection':'원본 파일의 측정값을 확인한다.'}
    criteria = [{**criterion(render_condition(spec)), 'document_condition':spec}]
    with make_client(tmp_path, runtime([])) as client:
        response = submit(client, criteria=criteria, image=image_bytes(image_format='JPEG'))
        result = client.get('/api/reviews/'+response.json()['id']).json()
        assert result['criteria'] == criteria
        assert result['results'][0]['status'] == 'pass'
        assert result['results'][0]['check_method'] == 'source_image_metadata'
        assert result['image']['original_mode'] == 'RGB' and result['image']['original_frames'] == 1
        assert result['image']['original_extension'] == '.jpg'


def test_stale_document_expectations_are_rejected_before_ocr(tmp_path):
    def unexpected(_path): pytest.fail('stale metadata must fail before OCR')
    spec = {'label':'행사 시간','requirement':'17:30','level':'필수','inspection':''}
    with make_client(tmp_path, unexpected) as client:
        assert submit(client,criteria=[{**criterion('행사 시간: [필수] 18:30'), 'document_condition':spec}]).status_code == 422


def test_merged_pdf_details_and_both_sources_survive_api_confirmation(tmp_path):
    spec = {'label':'상품명','requirement':'테스트 라떼','level':'명시 없음',
            'inspection':'원문 기준 ' + '가' * 210,
            'sources':[{'page':1,'source_id':'p1-s1','source':'상품명 | 테스트 라떼'},
                       {'page':2,'source_id':'p2-s3','source':'IMG-02 | 상품명 | 테스트 라떼'}]}
    criteria = [{**criterion('상품명: 테스트 라떼'), 'document_condition':spec}]
    with make_client(tmp_path, runtime([])) as client:
        response = submit(client, criteria=criteria)
        assert response.status_code == 202
        result = client.get('/api/reviews/'+response.json()['id']).json()
        assert result['criteria'][0]['document_condition'] == spec


@pytest.mark.parametrize("criteria,version", [
    ([], "1"), ([criterion("  ")], "1"), ([criterion("x" * 501)], "1"),
    ([criterion(), criterion()], "1"), ([criterion()], "0"), ([criterion()], "-2"),
    ([criterion(kind="other")], "1"), ([criterion(item_id=str(i)) for i in range(101)], "1"),
])
def test_invalid_frozen_criteria_rejected_before_ocr(tmp_path, criteria, version):
    def unexpected_call(_path):
        pytest.fail("Invalid input must not call OCR")
    with make_client(tmp_path, unexpected_call) as client:
        assert submit(client, criteria=criteria, version=version).status_code == 422
        assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("data", [b"not an image", b"%PDF-1.7 fake", b"<svg xmlns='http://www.w3.org/2000/svg' />", image_bytes(image_format="GIF")])
def test_non_raster_unsupported_or_corrupt_payloads_rejected(tmp_path, data):
    with make_client(tmp_path) as client:
        assert submit(client, image=data).status_code == 415


def test_upload_and_pixel_limits(tmp_path, monkeypatch):
    with make_client(tmp_path) as client:
        monkeypatch.setattr(backend, "MAX_UPLOAD_BYTES", 10)
        assert submit(client, image=b"x" * 11).status_code == 413
        monkeypatch.setattr(backend, "MAX_UPLOAD_BYTES", 1024 * 1024)
        monkeypatch.setattr(backend, "MAX_IMAGE_PIXELS", 100)
        assert submit(client, image=image_bytes(size=(11, 10))).status_code == 413
        assert submit(client, image=b"").status_code == 400


def test_exif_orientation_and_alpha_normalization(tmp_path):
    exif = Image.Exif()
    exif[274] = 6
    original = image_bytes(mode="RGBA", size=(6, 4), color=(255, 0, 0, 0), exif=exif)
    def inspect(path):
        with Image.open(path) as image:
            assert image.size == (8, 12) and image.mode == "RGB"
            assert image.getpixel((0, 0)) == (255, 255, 255)
        return {"records": [record("원문", box=[2, 4, 6, 10])], "status": "success", "input_dimensions": [8, 12]}
    with make_client(tmp_path, inspect) as client:
        response = submit(client, image=original)
        result = client.get(f"/api/reviews/{response.json()['id']}").json()
        assert result["status"] == "completed"
        assert (result["image"]["width"], result["image"]["height"]) == (4, 6)
        assert result["results"][0]["status"] == "review"
        assert result["ocr"]["records"][0]["bbox_xyxy"] == [1, 2, 3, 5]


def test_runtime_failures_are_failed_jobs_and_sanitized(tmp_path):
    def broken_runtime(_path):
        raise RuntimeError("secret_token=abc123 https://api.example/key /Users/private")
    with make_client(tmp_path, broken_runtime) as client:
        response = submit(client)
        result = client.get(f"/api/reviews/{response.json()['id']}").json()
        assert result["status"] == "failed" and result["stage"] == "failed"
        assert result["progress"] < 100 and result["results"] == []
        assert "OCR" in result["error"]
        assert "secret_token" not in json.dumps(result) and "/Users/private" not in json.dumps(result)


def test_bad_ocr_coordinate_contract_cannot_create_pass(tmp_path):
    def wrong_dimensions(_path):
        return {"records": [record("김천김밥축제")], "input_dimensions": [100, 150]}
    with make_client(tmp_path, wrong_dimensions) as client:
        response = submit(client)
        result = client.get(f"/api/reviews/{response.json()['id']}").json()
        assert result["status"] == "failed" and result["results"] == []


def test_remote_preprocessed_size_error_is_actionable_without_raw_error_text(tmp_path):
    class RemoteSizeError(RuntimeError):
        code = "remote_image_too_large"

    def too_large(_path):
        raise RemoteSizeError("secret endpoint/key not exposed")

    with make_client(tmp_path, too_large) as client:
        response = submit(client)
        result = client.get(f"/api/reviews/{response.json()['id']}").json()
        assert result["status"] == "failed"
        assert "7 MiB" in result["error"] and "로컬 실행" in result["error"]
        assert "secret" not in result["error"]


def test_health_does_not_expose_details_or_credentials(tmp_path):
    with make_client(tmp_path) as client:
        result = client.get("/api/health").json()
        assert result["runtime"]["ready"] is True
        assert result["runtime"]["device"] == "cpu"
        assert "detail" not in result["runtime"]
        assert client.get("/api/reviews/not-a-uuid").status_code == 404
        sample = client.get("/api/sample").json()
        assert "기간:" in sample["requirements"] and "로고" in sample["requirements"]
        assert client.get("/api/sample/image").status_code == 200


def test_restart_marks_interrupted_persisted_jobs_failed(tmp_path):
    with make_client(tmp_path) as client:
        identifier = submit(client).json()["id"]
    path = tmp_path / identifier / "result.json"
    interrupted = json.loads(path.read_text(encoding="utf-8"))
    interrupted.update(status="running", stage="ocr", progress=30)
    path.write_text(json.dumps(interrupted), encoding="utf-8")
    with make_client(tmp_path) as client:
        result = client.get(f"/api/reviews/{identifier}").json()
        assert result["status"] == "failed" and "재시작" in result["error"]


def test_missing_and_visual_constraints_never_get_false_pass_or_fail():
    results = review_criteria([
        criterion('문구: 존재하지 않는 문구', item_id="missing"),
        criterion('"김천김밥축제" 로고 위치 확인', item_id="visual_misclassified"),
        criterion('"김천김밥축제" 문구 사용 금지', item_id="negative"),
        criterion('문구: 김천김밥축제', kind="visual", item_id="visual"),
    ], [record("김천김밥축제")])
    assert {result["status"] for result in results} == {"review"}


def test_structured_date_and_title_conflicts_need_strong_unique_evidence():
    criteria = [criterion("기간: 2026.10.23 ~ 10.25", item_id="dates"), criterion()]
    matching = review_criteria(criteria, [record("2026.10.23 금 ~ 10.25 일", "date"), record("2026 김천 김밥 축제", "title")])
    assert [item["status"] for item in matching] == ["pass", "pass"]
    conflicting = review_criteria(criteria, [record("2026.10.24 ~ 10.25", "date"), record("다른축제", "title")])
    assert [item["status"] for item in conflicting] == ["fail", "fail"]
    uncertain = review_criteria(criteria, [record("2026.10.24 ~ 10.25", "date", .3), record("다른축제", "title", .3)])
    assert [item["status"] for item in uncertain] == ["review", "review"]
    multiple = review_criteria([criterion()], [record("김천김밥축제", "title"), record("다른축제", "other")])
    assert multiple[0]["status"] == "review"


def test_literal_quotes_need_all_requested_strings_and_preserve_evidence():
    records = [record("문구 가", "a"), record("문구 나", "b")]
    result = review_criteria([criterion('"문구 가"와 "문구 나" 표시', item_id="both")], records)[0]
    assert result["status"] == "pass"
    assert [e["ocr_id"] for e in result["evidence"]] == ["a", "b"]
    assert review_criteria([criterion('"문구 가"와 "없는 문구" 표시')], records)[0]["status"] == "review"


def test_numeric_literal_cannot_match_inside_a_different_number():
    result = review_criteria([criterion("가격: 3,000원")], [record("가격 13,000원")])[0]
    assert result["status"] == "review"
    result = review_criteria([criterion('"2026" 표시')], [record("20261")])[0]
    assert result["status"] == "review"


def test_source_preserves_a_full_500_character_frontend_requirement(tmp_path):
    original_requirement = "가" * 500
    selected = {**criterion(), "source": original_requirement}
    with make_client(tmp_path) as client:
        response = submit(client, criteria=[selected])
        assert response.status_code == 202
        result = client.get(f"/api/reviews/{response.json()['id']}").json()
        assert result["criteria"][0]["source"] == original_requirement
        selected["source"] += "나"
        assert submit(client, criteria=[selected]).status_code == 422


def test_scaled_input_pixel_cap_matches_runtime_limit():
    # Importing this module is side-effect-free; no model is imported/loaded.
    from backend.app.ocr_runtime import MAX_IMAGE_PIXELS as runtime_pixel_limit
    assert backend.MAX_IMAGE_PIXELS == 10_000_000
    assert backend.MAX_IMAGE_PIXELS * 4 <= runtime_pixel_limit
