"""계약 3.0.0 필드 표시: 위치 말, 값·단위, 관측 상태, 보고 줄. 계약 클래스 없이 값만 쓴다."""
from __future__ import annotations

import pytest

from backend.a07_reporter.details import detail_lines, facts, location_hint, value_text


@pytest.mark.parametrize(("bbox", "words"), [
    ((0.12, 0.35, 0.78, 0.82), "가운데"), ((0.08, 0.08, 0.88, 0.24), "위쪽"), ((0.4, 0.8, 0.6, 1.0), "아래쪽"),
    ((0.7, 0.4, 1.0, 0.6), "오른쪽"), ((0.0, 0.0, 0.2, 0.2), "왼쪽 위"), ((0.7, 0.7, 1.0, 1.0), "오른쪽 아래"),
    ((0.0, 0.0, 1.0, 1.0), "전체"), ((0.02, 0.03, 0.97, 0.99), "전체")])
def test_location_is_given_in_words_not_coordinates(bbox, words):
    assert location_hint(bbox) == words


@pytest.mark.parametrize(("kind", "value", "unit", "shown"), [
    ("number", "3", "개", "3개"), ("number", "10,000원", "원", "10,000원"), ("number", "3", None, "3"),
    ("text", "MORNING CUP", None, '"MORNING CUP"'), ("visual", "브랜드 로고", None, '"브랜드 로고"'),
    ("number", "", "개", None), ("text", "  ", None, None), ("text", None, None, None)])
def test_values_get_their_unit_once_and_text_is_quoted(kind, value, unit, shown):
    assert value_text(kind, value, unit) == shown


def test_failed_number_from_the_team_fixture_values():
    # A06 PR #53 checks/A07.fail.json의 requirement-number 값
    values = facts(kind="number", operator="equals", required=True, expected="3", expected_unit="개", observed="2",
                   observed_unit="개", observation_status="observed", bbox=(0.12, 0.35, 0.78, 0.82))
    assert values == {"required": "필수", "check": "숫자가 같아야 함", "expected": "3개", "observation": "읽음",
                      "observed": "2개", "location": "가운데"}
    assert detail_lines(values) == ("기준: 필수 · 숫자가 같아야 함 · 기준값 3개", "검사 모델이 읽은 값: 2개 (포스터 가운데)")


@pytest.mark.parametrize(("status", "said"), [
    ("absent", "포스터에서 찾지 못함"), ("unreadable", "읽을 수 없음"), ("not_checked", "검사하지 않음"),
    ("unknown", "관측 상태 모름"), ("something-new", "관측 상태 모름")])
def test_without_an_observed_value_only_the_status_is_reported(status, said):
    values = facts(kind="text", operator="contains", required=False, expected="MORNING CUP", observed="MORNING",
                   observation_status=status)
    assert "observed" not in values and "location" not in values and values["observation"] == said
    assert detail_lines(values) == ('기준: 권장 · 문구를 포함해야 함 · 기준값 "MORNING CUP"', f"검사 모델: {said}")


@pytest.mark.parametrize(("kind", "operator", "check"), [
    ("number", "gte", "기준값 이상이어야 함"), ("number", "lte", "기준값 이하여야 함"),
    ("visual", "present", "표시되어 있어야 함"), ("visual", "manual", "사람이 직접 확인"),
    ("unsupported", "manual", "사람이 직접 확인"), ("visual", "equals", "자동 검사 범위 밖")])
def test_every_check_kind_has_words(kind, operator, check):
    values = facts(kind=kind, operator=operator, required=True, expected="")
    assert values["check"] == check and "expected" not in values
    assert detail_lines(values)[0] == f"기준: 필수 · {check}"
