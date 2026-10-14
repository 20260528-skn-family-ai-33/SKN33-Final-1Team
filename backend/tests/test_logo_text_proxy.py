"""합의된 김천시 로고의 임시 문구 판정과 다른 시각 조건의 경계를 검증한다."""
import json

import pytest

from backend.app.review_rules import review_criteria
from backend.tests.test_review_api import criterion, make_client, runtime, submit
from backend.tests.test_review_rules_policy import line


LOGO = "김천시 로고가 표시되어 있어야 합니다."


def check_logo(records, text=LOGO):
    """실제 시각 기준의 ID와 유형을 유지한 채 임시 문구 판정을 실행한다."""
    return review_criteria([criterion(text, "visual", "logo")], records, multiline=True)[0]


@pytest.mark.parametrize("text", ["김천시", "김 천 시", "김천시 직지문화공원", "주최: 김천시"])
def test_logo_text_presence_passes_with_traceable_evidence(text):
    """합의한 문구 존재 기준은 로고 위치와 무관하게 글자와 원래 좌표를 근거로 삼는다."""
    record = line(text, .99, box=[70, 23, 127, 42])
    result = check_logo([record])
    assert result["status"] == "pass"
    assert result["criterion_id"] == "logo" and result["type"] == "visual"
    assert result["check_method"] == "logo_text_proxy"
    assert "임시 문구 기준" in result["reason"] and "심벌의 형태는 검증하지 않았습니다" in result["reason"]
    assert result["evidence"][0]["bbox_xyxy"] == record["bbox_xyxy"]
    assert result["evidence"][0]["ocr_id"] == record["id"]


@pytest.mark.parametrize("records,status", [
    ([line("김천시", .899)], "review"),
    ([line("김천시", .90)], "pass"),
    ([line("김천시", .60, engine="easyocr")], "pass"),
    ([line("김천시", .99, engine="qwen3-vl")], "review"),
    ([line("김천시", .99, requires_review=True)], "review"),
    ([line("김천시", .99, requires_review=True, selection_reason="refinement_incomplete")], "review"),
    ([line("김천시", .99, identifier="good"), line("김천시", .1, identifier="weak")], "pass"),
    ([line("김천시장 김천시청")], "review"),
    ([line("구미시")], "review"),
    ([line("김천", alternatives=[{"text": "김천시", "confidence": .99}])], "review"),
    ([], "review"),
])
def test_logo_proxy_requires_supported_current_text(records, status):
    """다른 단어·미검출·미검증 후보는 통과시키지 않고 기존 엔진 점수 기준을 따른다."""
    assert check_logo(records)["status"] == status


@pytest.mark.parametrize("text", [
    "김천시 로고가 하단에 표시되어 있어야 합니다.",
    "김천시 로고가 빨간색이어야 합니다.",
    "김천시 로고와 문구가 잘리지 않아야 합니다.",
    "김천시 로고가 표시되어 있어야 합니다. 문구도 잘리지 않아야 합니다.",
    "김천시 로고가 없어야 합니다.",
    "구미시 로고가 표시되어 있어야 합니다.",
    "문구와 이미지가 잘리지 않아야 합니다.",
])
def test_other_visual_conditions_are_not_replaced_by_logo_text(text):
    """문구가 보여도 합의하지 않은 로고·색상·배치·잘림·부정 조건은 확인 필요로 둔다."""
    result = check_logo([line("김천시 구미시")], text)
    assert result["status"] == "review" and "check_method" not in result


def test_api_persists_logo_proxy_without_promoting_clipping(tmp_path):
    """API 결과에도 문구 판정 사유와 좌표가 저장되고 잘림 항목은 독립적으로 보류된다."""
    criteria = [criterion(LOGO, "visual", "logo"),
                criterion("문구와 이미지가 잘리지 않아야 합니다.", "visual", "clipping")]
    with make_client(tmp_path, runtime([line("김천시", box=[20, 40, 180, 70])])) as client:
        response = submit(client, criteria=criteria)
        assert response.status_code == 202
        identifier = response.json()["id"]
        result = client.get(f"/api/reviews/{identifier}").json()
    assert result["status"] == "completed" and result["criteria"] == criteria
    assert [item["status"] for item in result["results"]] == ["pass", "review"]
    logo = result["results"][0]
    assert logo["check_method"] == "logo_text_proxy"
    assert logo["evidence"][0]["bbox_xyxy"] == [10, 20, 90, 35]
    persisted = json.loads((tmp_path / identifier / "result.json").read_text(encoding="utf-8"))
    assert persisted["results"] == result["results"]
