"""숏폼 검수 4·6·6-A단계 규칙(계획 문서의 완료 기준·판정표). 모델·영상 없이 순수 함수만 본다."""
from __future__ import annotations

import pytest

from backend.app.video_disclosure import classify_phrase, judge_disclosure, validate_input
from backend.app.video_judge import judge, mismatch_kind
from backend.app.video_tracks import build_segments, build_tracks, text_changed

BOX = [100, 1500, 980, 1600]


def frames(n, **extra):
    return [{"frame_id": f"t{t * 1000:06d}", "time_ms": t * 1000, "sampling": "base", "width": 1080, "height": 1920,
             "ocr_status": "ok", **extra} for t in range(n)]


def line(t, text, conf=.97, box=BOX, kept=True):
    return {"frame_id": f"t{t * 1000:06d}", "time_ms": t * 1000, "text": text, "confidence": conf,
            "bbox_xyxy": box, "role": "subtitle", "kept": kept}


@pytest.mark.parametrize("a,b,changed", [
    ("할인 50%", "할인 70%", True),          # 숫자는 정확히
    ("할인 50%", "할언 50%", False),         # 자모 1개 오인식은 묶는다
    ("공공누리 유형 확인!!", "공공누리 유형확인!", False),
    ("가", "나", True),                       # 4자모 미만은 허용 0
    ("행사 기간 안내", "주차장 위치 안내", True),
])
def test_text_changed(a, b, changed):
    assert text_changed(a, b) is changed


def test_tracks_merge_misreads_and_keep_variants():
    lines = [line(0, "공공누리 유형 확인!!"), line(1, "공공누리 유형확인!"), line(2, "공공누리 유형 확인!!"),
             line(3, "공공누리 유형 확인!!")]
    tracks = build_tracks(frames(4), lines, 4000)
    assert len(tracks) == 1 and tracks[0]["text"] == "공공누리 유형 확인!!"
    assert tracks[0]["variants"] == [{"text": "공공누리 유형확인!", "frame_id": "t001000", "time_ms": 1000}]
    assert tracks[0]["requires_review"] is False and tracks[0]["end_ms"] == 4000


def test_number_replacement_is_never_hidden():
    """50→70→50 교체: 같은 자리를 강한 다른 줄이 차지하면 기존 트랙을 닫는다(숫자 변경 미검출 0)."""
    texts = ["할인 50%"] * 3 + ["할인 70%"] * 2 + ["할인 50%"] * 3
    tracks = build_tracks(frames(8), [line(t, x) for t, x in enumerate(texts)], 8000)
    assert [(t["text"], t["start_ms"], t["end_ms"]) for t in tracks] == [
        ("할인 50%", 0, 3000), ("할인 70%", 3000, 5000), ("할인 50%", 5000, 8000)]
    segments = build_segments(tracks, frames(8), 8000, [])
    assert [(s["start_ms"], s["end_ms"], s["track_ids"]) for s in segments] == [
        (0, 3000, ["T001"]), (3000, 5000, ["T002"]), (5000, 8000, ["T003"])]


def test_vote_without_majority_requires_review():
    tracks = build_tracks(frames(2), [line(0, "할인 쿠폰 지급"), line(1, "할인 쿠폰 지금")], 2000)
    assert len(tracks) == 1 and tracks[0]["requires_review"] is True


def test_missing_frames_tolerance_and_failed_frames():
    gap = frames(5)
    gap[2]["ocr_status"] = "failed"  # OCR 실패 프레임은 누락으로 세지 않는다
    tracks = build_tracks(gap, [line(0, "행사 안내"), line(1, "행사 안내"), line(3, "행사 안내")], 5000)
    assert len(tracks) == 1
    tracks = build_tracks(frames(6), [line(0, "행사 안내"), line(4, "행사 안내")], 6000)
    assert len(tracks) == 2  # 정확 일치 트랙의 누락 허용은 2프레임


@pytest.mark.parametrize("expected,observed,kind", [
    ("할인 쿠폰", "할 인쿠폰", "spacing"), ("WHOLESALE", "WHOLE SALE", "spacing"), ("할인 50%", "할인 70%", "one_character"),
    ("할인", "할언", "one_character"), ("Sale", "SALE", "case"), ("할인 쿠폰", "할인 쿠폰", None),
])
def test_mismatch_kinds(expected, observed, kind):
    assert mismatch_kind(expected, observed) == kind


def _track(text, *, variants=(), conf=.99):
    return {"id": "T001", "text": text, "confidence": conf, "bbox_xyxy": BOX, "requires_review": False,
            "start_ms": 0, "end_ms": 3000, "readings": [{"frame_id": "t000000", "time_ms": 0, "text": text,
                                                       "confidence": conf, "bbox_xyxy": BOX}],
            "variants": [{"text": v, "frame_id": "t001000", "time_ms": 1000} for v in variants]}


SEGMENTS = [{"id": "S01", "start_ms": 0, "end_ms": 3000, "track_ids": ["T001"], "frame_ids": ["t000000"], "rep_frame_id": "t000000"}]


def test_judge_once_with_strict_mismatch():
    criteria = [{"id": "a", "text": '"할인 쿠폰"', "type": "information"}]
    clean = judge(criteria, [_track("할인 쿠폰")], SEGMENTS, frames(3))
    assert clean["calls"] == 1 and clean["results"][0]["status"] == "pass"
    assert "검사한 시점 기준" in clean["results"][0]["reason"]
    assert clean["results"][0]["evidence_segments"][0]["segment_id"] == "S01"
    spaced = judge([{"id": "a", "text": '"할인쿠폰"', "type": "information"}], [_track("할인 쿠폰")], SEGMENTS, frames(3))
    assert spaced["results"][0]["status"] == "review" and spaced["mismatch"][0]["kind"] == "spacing"
    varied = judge(criteria, [_track("할인 쿠폰", variants=["할언 쿠폰"])], SEGMENTS, frames(3))
    assert varied["results"][0]["status"] == "review"


def test_one_digit_change_hidden_by_ocr_spacing_is_mismatch():
    """실측: 기준 '2월 한정 70% 할인'에 화면은 '2월 한정50% 할인'(OCR이 띄어쓰기를 흘림)."""
    criteria = [{"id": "a", "text": '"2월 한정 70% 할인"', "type": "information"}]
    out = judge(criteria, [_track("2월 한정50% 할인")], SEGMENTS, frames(3))
    result = out["results"][0]
    assert result["status"] == "review" and result["mismatch"][0]["kind"] == "one_character"
    assert result["evidence_segments"] and result["whole_video"] is False


def test_judge_guards_lower_but_never_raise():
    title = [{"id": "t", "text": "행사명: 김천김밥축제", "type": "information"}]
    wrong = judge(title, [_track("함께 즐기는 축제")], SEGMENTS, frames(3))
    assert wrong["results"][0]["status"] in ("review", "pass") and wrong["results"][0]["status"] != "fail"
    negative = [{"id": "n", "text": "'최고' 표현은 쓰지 마세요", "type": "information"}]
    assert judge(negative, [_track("업계 최고")], SEGMENTS, frames(3))["results"][0]["status"] == "review"
    assert judge([], [_track("x")], SEGMENTS, frames(3))["calls"] == 0


@pytest.mark.parametrize("text,kind", [
    ("#광고", "allowed"), ("유료 광고 포함", "allowed"), ("#협찬", "allowed"), ("협찬: 김천시", "excluded"),
    ("체험단", "disallowed"), ("AD", "disallowed"), ("Sponsored", "disallowed"), ("광고 문의", "ambiguous"), ("맛있다", "none"),
])
def test_disclosure_lexicon(text, kind):
    assert classify_phrase(text) == kind


def _frames(duration):
    from backend.app.video_frames import plan_times
    return [{**p, "ocr_status": "ok"} for p in plan_times(duration)]


def _lines(frames, text, predicate=lambda t: True):
    return [{"frame_id": f["frame_id"], "time_ms": f["time_ms"], "text": text, "bbox_xyxy": [0, 0, 10, 10]}
            for f in frames if predicate(f["time_ms"])]


def test_disclosure_table():
    d = 30000
    fr = _frames(d)
    endorsement = validate_input({"kind": "endorsement", "title": "신상 리뷰"})
    everywhere = judge_disclosure(endorsement, fr, _lines(fr, "#광고"), d)
    assert everywhere["text_time_status"] == "pass" and everywhere["status"] == "review"  # R5·R6 남음
    edges_only = judge_disclosure(endorsement, fr, _lines(fr, "#광고", lambda t: t < 3000 or t >= d - 3000), d)
    assert edges_only["rules"]["R3c"]["status"] == "review" and edges_only["status"] == "review"  # 중간 미확인은 위반 아님
    nothing = judge_disclosure(endorsement, fr, [], d)
    assert nothing["status"] == "fail" and nothing["severity"] == "block"  # 조건이 모두 맞을 때만 위반
    unknown = judge_disclosure(validate_input({"kind": "unknown", "title": "x"}), fr, [], d)
    assert unknown["status"] == "review"
    failed = [dict(f, ocr_status="failed") if f["time_ms"] == 0 else f for f in fr]
    assert judge_disclosure(endorsement, failed, [], d)["status"] == "review"  # 창 OCR 실패면 위반 아님
    credit = judge_disclosure(endorsement, fr, _lines(fr, "협찬: 김천시"), d)
    assert credit["status"] == "fail"  # 크레딧은 표시 문구가 아님
    titled = judge_disclosure(validate_input({"kind": "endorsement", "title": "#광고 신상 리뷰"}), fr, [], d)
    assert titled["rules"]["R2"]["status"] == "pass" and titled["text_time_status"] == "pass"
    late = judge_disclosure(validate_input({"kind": "endorsement", "title": "신상 리뷰 영상입니다 꼭 보세요 #광고"}), fr, [], d)
    assert late["rules"]["R2"]["status"] == "review"
    assert judge_disclosure(validate_input({"kind": "self"}), fr, [], d)["status"] == "not_applicable"


@pytest.mark.parametrize("duration", [5000, 6000])
def test_short_video_merges_edge_windows(duration):
    fr = _frames(duration)
    result = judge_disclosure(validate_input({"kind": "endorsement"}), fr, _lines(fr, "#광고", lambda t: t == 0), duration)
    assert result["rules"]["R3c"]["status"] == "skipped" and result["text_time_status"] == "pass"
