from pathlib import Path

import pytest

from app.inspector import inspect_text_rules, unavailable_results, validate_image_path


RULES = [
    {"id": "C01", "expected": "딸기라떼"},
    {"id": "C02", "expected": "30% 할인"},
    {"id": "C03", "expected": "MORNING CUP"},
    {"id": "C04", "expected": 3},
    {"id": "C05", "expected": "분홍 우유"},
]


def lines(title="딸기라떼", discount="30% 할인", brand=True):
    data = [{"text": title, "bbox": [.3, .1, .4, .1], "confidence": 1},
            {"text": discount, "bbox": [.4, .2, .2, .05], "confidence": 1}]
    if brand:
        data.append({"text": "MORNING CUP", "bbox": [.35, .02, .3, .03], "confidence": 1})
    return data


def test_observed_text_determines_verdict_and_visual_rules_are_not_run():
    results = inspect_text_rules(lines(discount="20% 할인"), RULES)
    assert [r["verdict"] for r in results] == ["충족", "미충족", "충족", "확인 필요", "확인 필요"]
    assert results[3]["execution_status"] == "not_run"
    assert results[4]["execution_status"] == "not_run"


def test_title_typos_are_not_autocorrected_and_missing_brand_is_uncertain():
    results = inspect_text_rules(lines(title="딸기라테", brand=False), RULES)
    assert results[0]["verdict"] == "미충족"
    assert results[2]["verdict"] == "확인 필요"
    assert results[2]["observed"] == "검출되지 않음"


def test_empty_ocr_is_never_a_pass():
    assert all(r["verdict"] == "확인 필요" for r in inspect_text_rules([], RULES))


def test_cannot_read_outside_allowed_assets(tmp_path):
    path = tmp_path / "outside.png"
    path.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="허용 디렉터리"):
        validate_image_path(path)


def test_execution_failure_never_returns_a_content_verdict():
    results = unavailable_results(RULES, "OCR unavailable")
    assert all(result["verdict"] is None for result in results)
    assert all(result["execution_status"] == "failed" for result in results)
