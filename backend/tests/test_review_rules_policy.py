"""모델별 운영 기준과 기준 문구별 원문 보존 정책을 검증한다."""
import pytest

from backend.app.api import _map_records
from backend.app.model_ocr import model_candidate
from backend.app.review_rules import review_criteria


def line(text, score=.99, *, engine="paddleocr", identifier="line", box=None, **extra):
    """엔진·점수와 원본 좌표를 가진 최소 OCR 근거를 만든다."""
    return {"id": identifier, "text": text, "confidence": score,
            "bbox_xyxy": box or [10, 10, 590, 30], "recognition_engine": engine, **extra}


def check(text, records, *, kind="information"):
    """운영과 같은 여러 줄 해석을 적용한 하나의 검수 결과를 반환한다."""
    return review_criteria([{"id": "criterion", "text": text, "type": kind}], records, multiline=True)[0]


def test_hybrid_preserves_two_unchanged_places_but_not_the_corrected_place():
    """같은 OCR 줄의 다른 단어가 바뀌어도 기존 두 장소의 충분한 근거는 유지한다."""
    original = line("김천시 직지문화공원 사명대사공원 친환경생대공원 일원", .88, engine="easyocr")
    candidate = model_candidate(original, "김천시 직지문화공원, 사명대사공원, 친환경생태공원 일원", .93, "paddleocr", "one")
    # API 좌표·출처 매핑 이후에도 기준별 판단에 필요한 원문 메타데이터가 남아야 한다.
    records = _map_records({"records": [candidate]}, 300, 100)
    results = [check(f"장소: {place}", records) for place in ("직지문화공원", "사명대사공원", "친환경생태공원")]
    assert [result["status"] for result in results] == ["pass", "pass", "review"]
    assert results[0]["evidence"][0]["baseline_confidence"] == .88
    assert "변경된 문구" in results[2]["reason"]
    assert "신뢰도가 낮" not in results[2]["reason"]
    assert original["text"].endswith("친환경생대공원 일원")
    # 잘못 읽은 이전 장소도 신규 장소도 충돌 상태에서 자동 통과하지 않는다.
    assert check("장소: 친환경생대공원", records)["status"] == "review"


@pytest.mark.parametrize("score,status", [(.60, "review"), (.899, "review"), (.90, "pass"), (.99, "pass")])
def test_native_paddle_literal_uses_its_own_threshold(score, status):
    """Paddle 점수를 EasyOCR의 .60 기준에 그대로 대입하지 않는다."""
    result = check("장소: 직지문화공원", [line("직지문화공원", score)])
    assert result["status"] == status
    if status == "review":
        assert "해당 모델의 자동 판정 기준" in result["reason"]
    assert check("장소: 직지문화공원", [line("직지문화공원", .60, engine="easyocr")])["status"] == "pass"


@pytest.mark.parametrize("text", ["행사명: 김천김밥축제", "기간: 2026.10.23 ~ 10.25"])
def test_native_paddle_exact_structured_fields_pass(text):
    """충분한 Paddle 점수와 유일하고 유효한 제목·날짜가 함께 있어야 통과한다."""
    records = [line("2026 김천 김밥 축제", .90, identifier="title"),
               line("2026.10.23 금 ~ 10.25 일", .90, identifier="date", box=[10, 80, 590, 110])]
    assert check(text, records)["status"] == "pass"


@pytest.mark.parametrize("criterion,observed", [("행사명: 김천김밥축제", "다른축제"),
                                               ("기간: 2026.10.23 ~ 10.25", "2026.10.24 ~ 10.25")])
@pytest.mark.parametrize("score,status", [(.90, "review"), (.969, "review"), (.97, "fail")])
def test_native_paddle_conflicts_need_the_stricter_threshold(criterion, observed, score, status):
    """일치보다 더 엄격한 점수에 도달한 유일한 제목·기간만 불일치로 판정한다."""
    assert check(criterion, [line(observed, score)])["status"] == status


@pytest.mark.parametrize("records,criterion", [
    ([line("김천김밥축제", identifier="one"), line("다른축제", identifier="two", box=[10, 100, 500, 130])], "행사명: 김천김밥축제"),
    ([line("2026.10.23 ~ 10.25", identifier="one"), line("2026.10.24 ~ 10.25", identifier="two", box=[10, 100, 500, 130])], "기간: 2026.10.23 ~ 10.25"),
    ([line("2026.10.23 토 ~ 10.25 일")], "기간: 2026.10.23 ~ 10.25"),
    ([line("2026.02.30 ~ 03.01")], "기간: 2026.02.28 ~ 03.01"),
])
def test_native_paddle_ambiguity_and_invalid_dates_never_auto_decide(records, criterion):
    """높은 점수라도 복수 후보·틀린 요일·존재하지 않는 날짜를 확정하지 않는다."""
    assert check(criterion, records)["status"] == "review"


@pytest.mark.parametrize("baseline_score,baseline_pending", [(.59, False), (.99, True)])
def test_hybrid_agreement_cannot_promote_an_insufficient_baseline(baseline_score, baseline_pending):
    """두 모델의 일치만으로 낮은 원문 점수나 기존 보류를 해제하지 않는다."""
    original = line("직지문화공원", baseline_score, engine="easyocr", requires_review=baseline_pending)
    candidate = model_candidate(original, "직지문화공원", .999, "paddleocr", "one")
    result = check("장소: 직지문화공원", [candidate])
    assert result["status"] == "review"
    assert "자동 확정할 근거" in result["reason"]


@pytest.mark.parametrize("criterion,original_text,candidate_text", [
    ("행사명: 김천김밥축제", "김천김밤축제", "김천김밥축제"),
    ("기간: 2026.10.23 ~ 10.25", "2026.10.24 ~ 10.25", "2026.10.23 ~ 10.25"),
])
def test_hybrid_changed_title_and_date_remain_review(criterion, original_text, candidate_text):
    """제목·날짜가 모델 사이에서 바뀌었다면 신규 고점수로 원문 충돌을 지우지 않는다."""
    candidate = model_candidate(line(original_text, engine="easyocr"), candidate_text, .999, "paddleocr", "one")
    result = check(criterion, [candidate])
    assert result["status"] == "review"
    assert "변경된 문구" in result["reason"]


@pytest.mark.parametrize("criterion,text", [("행사명: 김천김밥축제", "김천 김밥 축제"),
                                          ("기간: 2026.10.23 ~ 10.25", "2026.10.23 금 ~ 10.25 일")])
def test_hybrid_unchanged_unique_structured_evidence_retains_pass(criterion, text):
    """기존에 해석 가능했던 유일한 제목·날짜가 유지되면 원래 근거로 통과한다."""
    candidate = model_candidate(line(text, .9, engine="easyocr"), text, .95, "paddleocr", "one")
    assert check(criterion, [candidate])["status"] == "pass"


@pytest.mark.parametrize("reason", ["refinement_incomplete", "model_abstained_keep_original", "conflicting_candidates_keep_original"])
def test_incomplete_abstained_or_conflicting_rereads_cannot_reuse_baseline(reason):
    """재인식 실패·기권·명시 충돌은 원문 보존 예외로 자동 통과하지 못한다."""
    candidate = model_candidate(line("직지문화공원", engine="easyocr"), "직지문화공원", .99, "paddleocr", "one")
    candidate["selection_reason"] = reason
    assert check("장소: 직지문화공원", [candidate])["status"] == "review"


def test_legacy_candidate_without_original_score_and_unknown_engine_remain_review():
    """기록되지 않은 원문 점수를 추측하거나 다른 엔진에 기본 임계값을 적용하지 않는다."""
    candidate = model_candidate(line("직지문화공원", engine="easyocr"), "직지문화공원", .99, "paddleocr", "one")
    del candidate["baseline_confidence"]
    assert check("장소: 직지문화공원", [candidate])["status"] == "review"
    assert check("장소: 직지문화공원", [line("직지문화공원", engine="qwen3-vl")])["status"] == "review"


def test_native_paddle_visual_absence_and_unmatched_text_still_need_review():
    """문자 점수로 로고·잘림·금지·OCR 미검출 조건까지 확정하지 않는다."""
    records = [line("김천김밥축제 직지문화공원", 1.0)]
    for text, kind in [("김천김밥축제 로고 표시", "visual"), ("문구와 이미지가 잘리지 않아야 함", "visual"),
                       ('"김천김밥축제" 사용 금지', "information"), ("장소: 사명대사공원", "information")]:
        assert check(text, records, kind=kind)["status"] == "review"


def test_multiple_requested_strings_need_independent_support_for_every_string():
    """같은 줄의 유지 문구 하나가 다른 수정·누락 문구까지 통과시키지 못한다."""
    candidate = model_candidate(line("직지문화공원 친환경생대공원", .99, engine="easyocr"),
                                "직지문화공원 친환경생태공원", .99, "paddleocr", "one")
    changed = check('"직지문화공원"과 "친환경생태공원" 표시', [candidate])
    assert changed["status"] == "review" and "변경된 문구" in changed["reason"]
    missing = check('"직지문화공원"과 "없는문구" 표시', [candidate])
    assert missing["status"] == "review" and "전체를 OCR에서 확인하지 못" in missing["reason"]
    assert "후보에만" not in missing["reason"]


def test_candidate_only_string_is_distinct_from_entirely_missing_string():
    """현재 선택되지 않은 후보에만 요청 문구가 남은 경우에만 그 사유를 안내한다."""
    record = line("직지문화공원 친환경생대공원", requires_review=True,
                  alternatives=[{"text": "직지문화공원 친환경생태공원", "confidence": .99}])
    result = check('"직지문화공원"과 "친환경생태공원" 표시', [record])
    assert result["status"] == "review" and "후보에만" in result["reason"]
