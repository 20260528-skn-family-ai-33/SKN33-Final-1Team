"""팀 A07 보고서 템플릿(report_template.json, creative-checker.a07.brief-report)의 코드표와 문장 규칙.

- report_template.json은 팀 템플릿 원본(Downloads/A07_보고서_템플릿/A07_보고서_템플릿.json)을 그대로 복사한 것이다.
  말(판정·관측 상태·사유·한계·할 일 문장)은 이 파일에서만 읽는다. 프런트 reportBrief.js도 같은 표를 시험으로 대조한다.
- 조사(을(를)·이(가)·은(는)·와(과)·(으)로)는 앞 글자의 받침으로 하나를 고른다. 따옴표·괄호·문장부호는 건너뛴다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

TEMPLATE = json.loads(Path(__file__).with_name("report_template.json").read_text(encoding="utf-8"))
CODES = TEMPLATE["codes"]
VERDICT: dict[str, str] = CODES["verdict"]
OBSERVATION: dict[str, str] = CODES["observation"]
FIELD_LABEL: dict[str, str] = CODES["field_label"]
ISSUE_REASON: dict[str, str] = CODES["issue_reason"]
REASON_PRIORITY: tuple[str, ...] = tuple(CODES["reason_priority"])
LIMITATION_KO: dict[str, str] = CODES["limitation_ko"]
ACTION_RULES: dict[str, str] = CODES["action_rules"]

# 판정별로 쓸 수 있는 할 일 규칙. 불충족은 고칠 일, 확인 필요는 볼 일만 고른다(다르면 기본 문장).
FAIL_RULES = ("value_mismatch", "observed_absence", "unit_mismatch")
REVIEW_RULES = ("date_comparison_review_required", "qualifier_interpretation_required", "missing_expected_value",
                "typed_attribute_review_required")
LABEL_LIMIT = 20

_PARTICLE = re.compile(r"을\(를\)|이\(가\)|은\(는\)|와\(과\)|\(으\)로")
_SKIP = set(" \t'\"‘’“”()[]{}<>「」『』.,!?…·:;~-")
_DIGIT_FINAL = {"0": 21, "1": 8, "3": 16, "6": 1, "7": 8, "8": 8}   # 영·일·삼·육·칠·팔의 받침(ㄹ=8)
_LETTER_FINAL = {"l": 8, "m": 16, "n": 4, "r": 8}                   # 엘·엠·엔·알


def _final(text: str) -> int | None:
    """끝 글자의 받침 번호(0=없음, 8=ㄹ). 판단할 글자가 없으면 None."""
    for char in reversed(text):
        if char in _SKIP:
            continue
        if "가" <= char <= "힣":
            return (ord(char) - 0xAC00) % 28
        if char.isdigit():
            return _DIGIT_FINAL.get(char, 0)
        if char.isascii() and char.isalpha():
            return _LETTER_FINAL.get(char.lower(), 0)
        return None
    return None


def particle(before: str, marker: str) -> str:
    """marker는 '을(를)' 꼴. 받침을 모르면 괄호 쓰기 그대로 둔다."""
    final = _final(before)
    if final is None:
        return marker
    if marker == "(으)로":
        return "로" if final in (0, 8) else "으로"
    outside, inside = marker[0], marker[2]
    with_final, without = (inside, outside) if marker == "와(과)" else (outside, inside)   # 와(과)만 괄호 안이 받침형
    return with_final if final else without


def fill(rule: str, **values: str) -> str:
    text = rule.format(**values)
    out, last = [], 0
    for match in _PARTICLE.finditer(text):
        out.append(text[last:match.start()])
        out.append(particle("".join(out), match.group()))
        last = match.end()
    return "".join(out) + text[last:]


def reason_code(issues) -> str | None:
    """사유 코드 가운데 reason_priority에서 가장 앞선 하나. 모르는 코드만 있으면 그 첫 코드."""
    known = [code for code in REASON_PRIORITY if code in issues]
    return known[0] if known else (issues[0] if issues else None)


def reason_text(code: str | None) -> str:
    if code is None:
        return ""
    return ISSUE_REASON.get(code) or ISSUE_REASON["_unknown"].format(code=code)


def limitation_text(text: str) -> str:
    return LIMITATION_KO.get(text, text)   # 모르는 문구는 그대로 옮긴다(템플릿 _unknown)


def short_label(text: str) -> str:
    """기준 문장 앞 20자. A07 입력에는 기준 목록(presentation.label)이 없어서 이것으로 이름을 대신한다."""
    text = " ".join(text.split())
    return text if len(text) <= LABEL_LIMIT else text[:LABEL_LIMIT].rstrip() + "…"


def action_text(verdict: str, code: str | None, label: str, expected: str | None) -> str:
    allowed = FAIL_RULES if verdict == "fail" else REVIEW_RULES
    rule = ACTION_RULES.get(code) if code in allowed else None
    if rule is None or ("{expected}" in rule and not expected):
        rule = ACTION_RULES["_default_fail" if verdict == "fail" else "_default_review"]
    return fill(rule, label=label, labels=label, expected=expected or "")


def groupable(verdict: str, code: str | None) -> bool:
    """사유가 같은 '확인'은 한 줄로 묶는다. 기준값을 넣는 문장은 항목마다 쓴다."""
    if verdict != "review":
        return False
    rule = ACTION_RULES.get(code) if code in REVIEW_RULES else ACTION_RULES["_default_review"]
    return "{expected}" not in rule
