"""좌표 결합·재시도 상한·후보 충돌·API 연결을 모델 없이 검증한다."""
import copy
import json
import subprocess

import pytest
from PIL import Image

from backend.app import adaptive_ocr as adaptive
from backend.app.api import _map_records
from backend.app.event_layout import extract_event_multiline, parse_range
from backend.app.review_rules import review_criteria


def record(text, identifier="r1", box=None, confidence=.95):
    """텍스트와 좌표를 독립적으로 지정할 수 있는 OCR 근거를 만든다."""
    box = box or [10, 10, 130, 40]
    x1, y1, x2, y2 = box
    return {"id": identifier, "text": text, "confidence": confidence, "bbox_xyxy": box,
            "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]}


@pytest.mark.parametrize("scale,dx,dy", [(1, 0, 0), (2, 330, 700), (.5, 50, 100)])
def test_title_composition_is_independent_of_absolute_position(scale, dx, dy):
    """두세 줄 제목이 위치·크기가 바뀌어도 동일한 ID 근거로 결합되는지 확인한다."""
    records = [record(t, str(i), [10, 10 + i * 33, 130, 40 + i * 33]) for i, t in enumerate(["새봄", "문화", "축제"])]
    for r in records:
        r["bbox_xyxy"] = [v * scale + (dx if i % 2 == 0 else dy) for i, v in enumerate(r["bbox_xyxy"])]
    event = extract_event_multiline(records)
    assert event["fields"]["event_name"]["value"] == "새봄문화축제"
    assert event["fields"]["event_name"]["source_ocr_ids"] == ["0", "1", "2"]
    assert event["groups"][0]["source_boxes"] == [r["bbox_xyxy"] for r in records]


def test_title_conflicts_columns_and_intervening_text_are_not_guessed():
    """다른 행사·열·중간 잡음을 지나 임의의 전체 제목을 만들지 않는다."""
    for records in [
        [record("봄축제", "a"), record("여름축제", "b", [10, 43, 130, 73])],
        [record("봄", "a"), record("축제", "b", [300, 43, 420, 73])],
        [record("봄", "a"), record("??", "noise", [10, 35, 130, 55]), record("축제", "b", [10, 60, 130, 90])],
        [record("봄", "a", [10, 10, 70, 40]), record("여름", "b", [75, 10, 140, 40]), record("축제", "c", [10, 43, 140, 73])],
    ]:
        assert extract_event_multiline(records)["fields"]["event_name"]["value"] is None


def test_existing_single_line_year_prefix_and_unparenthesized_weekday():
    """한 줄 제목의 선행 연도 제거와 괄호 없는 요일 검증을 유지한다."""
    assert extract_event_multiline([record("2026 봄 축제")])["fields"]["event_name"]["value"] == "봄축제"
    assert extract_event_multiline([record("2026.1.23 금 ~ 1.25 일")])["fields"]["end_date"]["value"] == "2026-01-25"
    assert extract_event_multiline([record("2026.1.23 금 ~ 1.24 일")])["fields"]["end_date"]["value"] is None


@pytest.mark.parametrize("text,expected", [
    ("2026.1.23(금) ~ 25.(일)", [("2026-01-23", "2026-01-25")]),
    ("2026년 5월 24일 ~ 31일", [("2026-05-24", "2026-05-31")]),
    ("2026-12-31 ~ 2027-01-01", [("2026-12-31", "2027-01-01")]),
    ("5.24 ~ 5.31", []),
    ("2026.5.24 5.31", []),
])
def test_date_normalization_only_inherits_observed_year_and_month(text, expected):
    """인쇄된 시작 연도·월만 상속하며 구분 기호를 만들어내지 않는다."""
    assert parse_range(text) == expected


@pytest.mark.parametrize("text", ["2026.2.30 ~ 3.1", "2026.1.23(금) ~ 25(월)", "2026.12.31 ~ 1.1"])
def test_invalid_calendar_weekday_and_implicit_rollover_remain_unknown(text):
    """실제 날짜·요일·순서가 맞지 않는 범위는 확정하지 않는다."""
    assert extract_event_multiline([record(text)])["fields"]["start_date"]["value"] is None


def test_split_year_and_dates_preserve_all_ids_and_reject_multiple_ranges():
    """나뉜 연도·날짜를 연결하고 서로 다른 유효 기간은 충돌로 남긴다."""
    records = [record("2026", "year"), record("5.24 -", "start", [10, 43, 130, 73]),
               record("5.31", "end", [10, 76, 130, 106])]
    fields = extract_event_multiline(records)["fields"]
    assert fields["start_date"]["value"] == "2026-05-24"
    assert fields["end_date"]["source_ocr_ids"] == ["year", "start", "end"]
    records.append(record("2026.6.1 ~ 6.2", "other", [300, 300, 450, 330]))
    assert extract_event_multiline(records)["fields"]["start_date"]["value"] is None


def test_crop_round_trip_and_clipping_are_exact():
    """경계 여백·실제 반올림 배율을 사용해 crop 좌표를 정확히 복원한다."""
    rgb = Image.new("RGB", (300, 200), "white")
    crop, transform = adaptive.prepare_crop(rgb, [0, 3, 299, 190])
    restored = adaptive.restore_candidate(record("글자", box=[0, 0, crop.width, crop.height]), transform, "r1")
    assert restored["bbox_xyxy"] == pytest.approx(transform["crop_box_input"])
    with pytest.raises(ValueError):
        adaptive.prepare_crop(rgb, [-1, 0, 20, 30])


def test_high_confidence_conflict_never_becomes_auto_pass():
    """기존 고신뢰도 문구와 재인식이 충돌하면 틀린 문구도 자동 통과하지 못한다."""
    original = record("친환경생대공원")
    result = adaptive.choose_candidate(original, record("친환경생태공원", confidence=.99), "crop-1")
    assert original["text"] == "친환경생대공원" and "alternatives" not in original
    assert result["requires_review"] and result["text"] == original["text"]
    verdict = review_criteria([{"id": "a", "text": "장소: 친환경생대공원", "type": "information"}], [result], multiline=True)
    assert verdict[0]["status"] == "review"
    assert verdict[0]["evidence"][0]["alternatives"][0]["text"] == "친환경생태공원"
    correct = review_criteria([{"id": "a", "text": "장소: 친환경생태공원", "type": "information"}], [result], multiline=True)
    assert correct[0]["status"] == "review" and correct[0]["evidence"]


def test_low_confidence_candidate_remains_unverified_and_agreement_does_not_boost():
    """더 선명한 후보를 제안해도 자동 승인하지 않고 일치 결과의 점수도 올리지 않는다."""
    candidate = adaptive.choose_candidate(record("탈출페스터벌", confidence=.2), record("탈춤페스티벌", confidence=.9), "crop")
    assert candidate["text"] == "탈춤페스티벌" and candidate["requires_review"]
    same = adaptive.choose_candidate(record("봄축제", confidence=.4), record("봄 축제", confidence=.99), "crop")
    assert same["confidence"] == .4


def test_retry_limits_failure_and_budget_preserve_baseline(tmp_path):
    """재인식 실패·예산 소진을 기록하고 원래 OCR과 시도 상한을 유지한다."""
    path = tmp_path / "input.png"
    Image.new("RGB", (400, 600), "white").save(path)
    records = [record("미상문구", str(i), [10, i * 35, 200, i * 35 + 30], .1) for i in range(12)]
    baseline = {"status": "success", "records": records, "input_dimensions": [400, 600]}
    original = copy.deepcopy(baseline)
    calls = []

    def broken(rgb, *, region):
        """응답 실패를 재현하여 반복 호출이 무한히 늘지 않는지 확인한다."""
        calls.append(region)
        raise RuntimeError("failure")

    result = adaptive.refine(path, baseline, tmp_path, read=broken)
    assert len(calls) == 9 and sum(calls) == adaptive.MAX_CROPS
    assert result["refinement_status"] == "partial" and baseline == original
    assert all(r["requires_review"] for r in result["records"])
    calls.clear()
    result = adaptive.refine(path, baseline, tmp_path, read=broken, budget=0)
    assert not calls and all(a["status"] == "skipped" for a in result["refinements"])


def test_hard_timeout_recovers_baseline_and_never_reuses_old_directory(tmp_path, monkeypatch):
    """중단된 프로세스의 저장된 원문만 복구하고 기존 실행은 덮어쓰지 않는다."""
    monkeypatch.setenv("RUN_TARGET", "local")
    output = tmp_path / "run"

    def timeout(command, **kwargs):
        """추론 대기 대신 기준선 저장 직후 시간 초과를 발생시킨다."""
        assert kwargs["timeout"] == 1 and kwargs["env"]["OCR_ALLOW_DOWNLOAD"] == "0"
        adaptive.save_json(output / "baseline.json", {"records": [record("봄축제")], "status": "success"})
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    result = adaptive.recognize_adaptive(tmp_path / "input.png", output, timeout=1)
    assert result["refinement_status"] == "timeout" and result["records"][0]["requires_review"]
    with pytest.raises(FileExistsError):
        adaptive.recognize_adaptive(tmp_path / "input.png", output)


def test_api_scales_alternatives_once_and_retains_review_guard():
    """후보 박스·원문·검토 플래그가 API의 좌표 복원에서 보존되는지 확인한다."""
    candidate = adaptive.choose_candidate(record("다른문구", box=[20, 40, 180, 70]),
                                          record("새문구", box=[20, 40, 180, 70]), "crop")
    result = _map_records({"records": [candidate], "input_dimensions": [200, 300]}, 100, 150)[0]
    assert result["bbox_xyxy"] == result["alternatives"][0]["bbox_xyxy"] == [10, 20, 90, 35]
    assert result["requires_review"] and result["baseline_text"] == "다른문구"


def test_api_uses_multiline_fields_and_preserves_private_attempts(tmp_path):
    """실제 API 흐름에서 결합된 제목의 각 줄 좌표와 비공개 원시 결과를 확인한다."""
    from backend.tests.test_review_api import make_client, submit, criterion

    def recognize(path):
        """모델 실행 없이 개선 경로의 응답 계약만 제공한다."""
        return {"status": "success", "pipeline": "adaptive_v1", "refinement_status": "completed",
                "records": [record("봄", "a", [20, 20, 180, 50]), record("문화축제", "b", [20, 53, 180, 83])],
                "input_dimensions": [200, 300]}

    with make_client(tmp_path, recognize) as client:
        identifier = submit(client, criteria=[criterion("행사명: 봄문화축제")]).json()["id"]
        result = client.get(f"/api/reviews/{identifier}").json()
    assert result["results"][0]["status"] == "pass"
    assert len(result["results"][0]["evidence"]) == 2
    assert result["ocr"]["event"]["fields"]["event_name"]["source_ocr_ids"] == ["a", "b"]
    assert (tmp_path / identifier / "ocr.raw.json").exists()
