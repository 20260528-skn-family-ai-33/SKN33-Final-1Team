"""실제 모델을 내려받지 않고 비교 도구의 데이터 분리·좌표·실패 처리를 검증한다."""
import copy
import json
import os
from pathlib import Path
import subprocess

from PIL import Image
import pytest

from scripts import benchmark_ocr as bench
from scripts.ocr_metrics import evaluate, validate_label


def record(text="봄축제", box=None, confidence=0.95):
    """가짜 인식 결과에도 실제 계약과 동일한 박스·다각형을 제공한다."""
    box = box or [2, 2, 18, 8]
    x1, y1, x2, y2 = box
    return {"id": "ocr_000", "text": text, "confidence": confidence, "bbox_xyxy": box,
            "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]}


def label_for(digest):
    """보이는 값·판독 불가·실제 부재가 서로 다른 참조 상태임을 고정한다."""
    return {"source_sha256": digest, "dimensions": [20, 30], "coordinate_space": "normalized_original_pixels",
            "human_verified": False, "review_status": "needs_human_review",
            "regions": [{"id": "title", "text": "봄축제", "bbox_xyxy": [0, 0, 20, 10]}],
            "fields": {"event_name": {"value": "봄축제", "status": "visible", "region_ids": ["title"]},
                       "start_date": {"value": None, "status": "unresolved", "region_ids": []},
                       "end_date": {"value": None, "status": "absent", "region_ids": []}},
            "criteria": [{"id": "title", "text": "행사명: 봄축제", "type": "information", "expected_status": "pass", "region_ids": ["title"]},
                         {"id": "visual", "text": "잘림 없음", "type": "visual", "expected_status": "review", "region_ids": []}]}


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    """서로 다른 두 원본으로 비교용·최종용 입력을 나눈다."""
    image_dir = tmp_path / "data/img"
    image_dir.mkdir(parents=True)
    cases = []
    for name, split, color in [("a", "tuning", "white"), ("b", "holdout", "blue")]:
        source = image_dir / f"{name}.png"
        Image.new("RGB", (20, 30), color).save(source)
        digest = bench.sha256(source)
        label = tmp_path / f"{name}.json"
        label.write_text(json.dumps(label_for(digest)))
        cases.append({"id": name, "image": f"data/img/{name}.png", "sha256": digest,
                      "split": split, "label": f"{name}.json"})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": "poster.benchmark.v1", "cases": cases}))
    monkeypatch.setattr(bench, "environment_metadata", lambda pipeline="baseline": {"test": True, "code_sha256": {
        "scripts/benchmark_ocr.py": bench.sha256(bench.PROJECT_ROOT / "scripts/benchmark_ocr.py")}})
    return tmp_path, manifest, cases


def successful_worker(path, directory, timeout):
    """원본 파일명과 라벨 없이 픽셀 크기만 읽는 대체 작업 프로세스다."""
    assert path.name == "input.png"
    assert not (directory / "label.snapshot.json").exists()
    with Image.open(path) as image:
        scale = image.width / 20
        return {"status": "success", "load_seconds": 1, "inference_seconds": 2, "wall_seconds": 3,
                "peak_rss_bytes": 1024, "raw": {"engine": "easyocr", "execution_target": "local", "device": "cpu",
                "status": "success", "input_dimensions": list(image.size),
                "records": [record(box=[v * scale for v in [2, 2, 18, 8]])]}}


def test_runs_are_isolated_and_holdout_is_not_sent_to_worker(dataset, monkeypatch):
    """재실행 산출물이 섞이지 않고 최종용 입력이 보존되는지 확인한다."""
    root, manifest, cases = dataset
    calls = []

    def fake_worker(path, directory, timeout):
        """작업 경로만 수집해 최종용 입력이 호출되지 않았는지 확인한다."""
        calls.append(path)
        return successful_worker(path, directory, timeout)

    monkeypatch.setattr(bench, "run_worker", fake_worker)
    first, report = bench.run_benchmark(manifest, root / "out", project=root)
    second, other = bench.run_benchmark(manifest, root / "out", project=root, variants=["rgb_2x"])
    assert first != second
    assert len(report["runs"]) == 3 and len(other["runs"]) == 1
    assert all(path.parent.parent.name == "a" for path in calls)
    assert report["reserved_case_ids"] == ["b"] and not (first / "b").exists()
    assert report["source_unchanged"] and report["status"] == "completed"
    assert (first / "a/source.bin").read_bytes() == (root / cases[0]["image"]).read_bytes()
    assert (first / "a/label-preview.png").exists()
    assert bench.sha256(first / "code.snapshot/scripts/benchmark_ocr.py") == report["environment"]["code_sha256"]["scripts/benchmark_ocr.py"]
    assert report["runs"][0]["evaluation"]["counts"]["exact_fields"] == 1
    assert report["summary"][0]["metrics"]["human_approved"]["evaluated_runs"] == 0


@pytest.mark.parametrize("variant,scale", [("rgb_1x", 1), ("rgb_2x", 2), ("gray_2x", 2)])
def test_exif_alpha_normalization_and_coordinate_round_trip(tmp_path, variant, scale):
    """회전·투명 배경·배율을 적용해도 화면 좌표로 정확히 복원되는지 확인한다."""
    source = tmp_path / "oriented.png"
    pixels = Image.new("RGBA", (20, 30), (255, 0, 0, 0))
    pixels.putpixel((0, 0), (0, 0, 255, 255))
    exif = pixels.getexif()
    exif[274] = 6
    pixels.save(source, exif=exif)
    before = source.read_bytes()
    rgb, metadata = bench.normalize_image(source)
    assert rgb.size == (30, 20) and rgb.getpixel((0, 0)) == (255, 255, 255)
    assert rgb.getpixel((29, 0)) == (0, 0, 255)
    image, transform = bench.prepare_variant(rgb, variant)
    raw = {"engine": "easyocr", "execution_target": "local", "device": "cpu", "status": "success",
           "input_dimensions": list(image.size), "records": [record(box=[v * scale for v in [2, 3, 18, 12]])]}
    mapped = bench.map_records(raw, rgb.size, scale)
    assert mapped[0]["bbox_xyxy"] == [2, 3, 18, 12]
    assert mapped[0]["polygon"] == [[2, 3], [18, 3], [18, 12], [2, 12]]
    assert transform["input_to_normalized_matrix"][0][0] == 1 / scale
    assert metadata["oriented_dimensions"] == [30, 20] and source.read_bytes() == before


@pytest.mark.parametrize("change", ["dimensions", "polygon", "box", "confidence", "engine"])
def test_invalid_ocr_output_is_rejected(change):
    """잘못된 엔진·좌표·신뢰도 응답을 성능 지표로 채택하지 않는다."""
    raw = {"engine": "easyocr", "execution_target": "local", "device": "cpu", "status": "success",
           "input_dimensions": [20, 30], "records": [record()]}
    if change == "dimensions":
        raw["input_dimensions"] = [40, 60]
    elif change == "polygon":
        raw["records"][0]["polygon"][0][0] = float("nan")
    elif change == "box":
        raw["records"][0]["bbox_xyxy"] = [0, 0, 21, 20]
    elif change == "confidence":
        raw["records"][0]["confidence"] = 4
    else:
        raw["engine"] = "unknown"
    with pytest.raises(ValueError):
        bench.map_records(raw, (20, 30), 1)


def test_paddle_only_benchmark_keeps_engine_and_uses_no_baseline(tmp_path, monkeypatch):
    """단독 비교는 EasyOCR를 호출하지 않고 Paddle 근거와 좌표를 그대로 평가한다."""
    from backend.app import paddle_ocr, ocr_runtime, model_ocr
    from unittest.mock import Mock
    native = {**record(), "recognition_engine": "paddleocr"}
    raw = {"engine": "paddleocr", "pipeline": "paddle_full_v1", "execution_target": "local",
           "device": "cpu", "status": "success", "input_dimensions": [20, 30], "records": [native]}
    recognize = Mock(return_value=raw)
    monkeypatch.setattr(paddle_ocr, "recognize_paddle", recognize)
    monkeypatch.setattr(ocr_runtime, "recognize_image", lambda *a, **k: pytest.fail("EasyOCR 실행 금지"))
    monkeypatch.setattr(model_ocr, "baseline_once", lambda *a, **k: pytest.fail("기준선 실행 금지"))
    result = bench.run_worker(tmp_path / "input.png", tmp_path, 35, pipeline="paddle-only")
    assert result["status"] == "success"
    assert recognize.call_args.kwargs == {"timeout": 35}
    mapped = bench.map_records(result["raw"], (20, 30), 1)
    assert mapped[0]["recognition_engine"] == "paddleocr"
    assert mapped[0]["bbox_xyxy"] == native["bbox_xyxy"]
    assert "baseline_text" not in mapped[0]


def test_paddle_only_failure_is_not_retried_with_easyocr(tmp_path, monkeypatch):
    """Paddle 실패를 별도 실패 기록으로 남기고 비교 엔진을 몰래 바꾸지 않는다."""
    from backend.app import paddle_ocr, model_ocr
    from unittest.mock import Mock
    monkeypatch.setattr(paddle_ocr, "recognize_paddle", Mock(side_effect=RuntimeError("unavailable")))
    monkeypatch.setattr(model_ocr, "baseline_once", lambda *a, **k: pytest.fail("기준선 실행 금지"))
    result = bench.run_worker(tmp_path / "input.png", tmp_path, 10, pipeline="paddle-only")
    assert result["status"] == "failed" and result["error_type"] == "RuntimeError"
    assert "raw" not in result


@pytest.mark.parametrize("engine", [None, "unknown", "easyocr", 1, []])
def test_paddle_score_cannot_be_evaluated_as_easyocr(engine):
    """낮은 Paddle 점수가 출처 누락·변경으로 EasyOCR 기준을 통과하지 않게 한다."""
    raw = {"engine": "paddleocr", "execution_target": "local", "device": "cpu", "status": "success",
           "input_dimensions": [20, 30], "records": [record(confidence=.8)]}
    mapped = bench.map_records(raw, (20, 30), 1)
    assert mapped[0]["recognition_engine"] == "paddleocr"
    assert evaluate(mapped, label_for("hash"), multiline=True)["verdicts"][0]["status"] == "review"
    raw["records"][0]["recognition_engine"] = engine
    with pytest.raises(ValueError, match="engine"):
        bench.map_records(raw, (20, 30), 1)


def test_manifest_tampering_and_pixel_duplicates_stop_before_inference(dataset, monkeypatch):
    """변경된 이미지와 다른 인코딩의 중복 이미지가 사전 차단되는지 확인한다."""
    root, manifest, cases = dataset
    monkeypatch.setattr(bench, "run_worker", lambda *args: pytest.fail("Must not infer"))
    Image.new("RGB", (20, 30), "red").save(root / cases[0]["image"])
    with pytest.raises(ValueError, match="hash mismatch"):
        bench.run_benchmark(manifest, root / "out", project=root)
    assert not (root / "out").exists()
    with Image.open(root / cases[0]["image"]) as im:
        im.save(root / cases[1]["image"], compress_level=1)
    for case in cases:
        case["sha256"] = bench.sha256(root / case["image"])
        case["label"] = None
    manifest.write_text(json.dumps({"schema_version": "poster.benchmark.v1", "cases": cases}))
    with pytest.raises(ValueError, match="duplicate"):
        bench.load_cases(manifest, root)


def test_timeout_is_recorded_and_next_variant_runs(dataset, monkeypatch):
    """실패가 기록되고 나머지 입력은 계속 측정되는지 확인한다."""
    root, manifest, _ = dataset
    count = 0

    def fake_worker(path, directory, timeout):
        """첫 작업만 시간 초과시켜 실패가 성공으로 집계되지 않음을 확인한다."""
        nonlocal count
        count += 1
        return {"status": "failed", "error_type": "Timeout"} if count == 1 else successful_worker(path, directory, timeout)

    monkeypatch.setattr(bench, "run_worker", fake_worker)
    out, report = bench.run_benchmark(manifest, root / "out", project=root)
    assert count == 3 and report["status"] == "failed"
    assert report["summary"][0]["failed"] == 1
    assert report["summary"][0]["metrics"]["draft_diagnostic"]["core_cer"] is None
    assert (out / "report.md").exists()


def test_subprocess_has_timeout_and_receives_no_labels(tmp_path, monkeypatch):
    """작업 명령에 정답 없이 로컬 실행과 제한시간만 전달되는지 확인한다."""
    def timeout_run(command, **kwargs):
        """실제 대기 없이 제한시간·로컬 모드·다운로드 금지 전달을 검사한다."""
        assert kwargs["timeout"] == 3
        assert kwargs["env"]["RUN_TARGET"] == "local" and kwargs["env"]["OCR_ALLOW_DOWNLOAD"] == "0"
        assert "--manifest" not in command and "--label" not in command
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout_run)
    result = bench.run_worker(tmp_path / "input.png", tmp_path, 3)
    assert result["status"] == "failed" and result["error_type"] == "Timeout"


def test_runtime_missing_is_local_failure_without_download(tmp_path, monkeypatch):
    """의존성 누락 시 자동 설치·원격 전환 없이 실패하는지 확인한다."""
    from backend.app import ocr_runtime

    def missing(*, allow_download):
        """설치 누락이 있어도 다운로드나 원격 실행을 요청하지 않게 한다."""
        assert not allow_download and os.environ["RUN_TARGET"] == "local" and os.environ["OCR_ALLOW_DOWNLOAD"] == "0"
        raise ModuleNotFoundError("easyocr")

    monkeypatch.setenv("RUN_TARGET", "runpod")
    monkeypatch.setenv("OCR_ALLOW_DOWNLOAD", "1")
    monkeypatch.setattr(ocr_runtime, "warmup_models", missing)
    monkeypatch.setattr(ocr_runtime, "recognize_image", lambda *args: pytest.fail("Must not infer"))
    output = tmp_path / "result.json"
    assert bench.worker(tmp_path / "input.png", output) == 1
    assert json.loads(output.read_text())["error_type"] == "ModuleNotFoundError"


def test_high_confidence_wrong_text_can_be_false_pass_and_visual_stays_review():
    """높은 점수의 오인식도 오통과로 집계하고 시각 항목은 확인 대상으로 둔다."""
    label = label_for("hash")
    label["regions"][0]["text"] = "친환경생태공원"
    label["criteria"][0].update(text="장소: 친환경생대공원", expected_status="fail")
    result = evaluate([record("친환경생대공원")], label)
    assert result["counts"]["false_pass"] == 1 and result["counts"]["expected_fail"] == 1
    assert result["verdicts"][1]["status"] == "review"
    assert result["counts"]["visible_fields"] == 1  # 미라벨/실제 부재는 정확도 분모에서 제외한다.
    assert result["counts"]["missing_fields"] == 1
    assert result["counts"]["edits"] == 1


def test_draft_and_approved_metrics_never_mix():
    """승인 근거가 필요하며 초안과 확정 지표의 분모가 합쳐지지 않게 한다."""
    draft = evaluate([record()], label_for("hash"))
    approved = copy.deepcopy(draft)
    approved["reference_status"] = "human_approved"
    rows = [{"variant": "rgb_2x", "status": "success", "device": "cpu", "evaluation": e} for e in [draft, approved]]
    group = bench.summarize(rows)[0]
    assert group["metrics"]["draft_diagnostic"]["visible_fields"] == 1
    assert group["metrics"]["human_approved"]["visible_fields"] == 1
    with pytest.raises(ValueError, match="reviewer"):
        validate_label({**label_for("hash"), "human_verified": True}, "hash", [20, 30])
    with pytest.raises(ValueError, match="hash"):
        validate_label(label_for("hash"), "another", [20, 30])


def test_unlabeled_input_and_original_output_guard(dataset, monkeypatch):
    """원본 폴더를 보호하고 라벨 없는 입력의 정확도를 만들어내지 않는다."""
    root, manifest, cases = dataset
    cases[0]["label"] = None
    manifest.write_text(json.dumps({"schema_version": "poster.benchmark.v1", "cases": cases}))
    monkeypatch.setattr(bench, "run_worker", successful_worker)
    with pytest.raises(ValueError, match="outside data/img"):
        bench.run_benchmark(manifest, root / "data/img/generated", project=root)
    _, report = bench.run_benchmark(manifest, root / "out", project=root, variants=["rgb_2x"])
    assert "evaluation" not in report["runs"][0]
    assert report["summary"][0]["metrics"]["draft_diagnostic"]["evaluated_runs"] == 0


def test_empty_ocr_is_missing_not_correct_and_overlapping_regions_count_once():
    """빈 결과는 누락으로 남기고 겹친 영역에서 문자가 중복 채점되지 않게 한다."""
    label = label_for("hash")
    result = evaluate([], label)
    assert result["counts"]["exact_fields"] == 0 and result["counts"]["missing_fields"] == 1
    assert result["core_cer"] == 1
    label["regions"].append({"id": "large", "text": "주변", "bbox_xyxy": [0, 0, 20, 30]})
    result = evaluate([record()], label)
    assert sum(len(region["ocr_ids"]) for region in result["regions"]) == 1


@pytest.mark.parametrize("changed", ["label", "input_sha256", "source_sha256", "split", "repeat"])
def test_before_after_comparison_rejects_different_evaluation_conditions(dataset, monkeypatch, changed):
    """전후 보고서의 정답·입력·분할·반복 수가 다르면 비교를 거절한다."""
    from scripts.compare_benchmarks import compare, render
    root, manifest, _ = dataset
    monkeypatch.setattr(bench, "run_worker", successful_worker)
    first, _ = bench.run_benchmark(manifest, root / "out", project=root, variants=["rgb_2x"])
    second, report = bench.run_benchmark(manifest, root / "out", project=root, variants=["rgb_2x"])
    paths = [first / "report.json", second / "report.json"]
    assert "1/1" in render(compare(paths))
    if changed == "label":
        path = second / "a/label.snapshot.json"
        label = json.loads(path.read_text())
        label["fields"]["event_name"]["value"] = "여름축제"
        path.write_text(json.dumps(label))
    else:
        target = report if changed == "split" else report["runs"][0]
        target[changed] = 2 if changed == "repeat" else "different"
        paths[1].write_text(json.dumps(report))
    with pytest.raises(ValueError, match="differ"):
        compare(paths)
