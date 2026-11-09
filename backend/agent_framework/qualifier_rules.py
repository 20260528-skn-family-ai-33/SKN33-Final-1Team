"""A06 allowance rules: settle only what code can settle from a criterion's own qualifier quotes.

Sorting is by the source's wording, never by meaning:
- needs a person: a poster alone cannot settle it, so an exact match still goes to review;
- format only: every sentence either grants named format differences or only keeps the value,
  so those differences are removed before comparing and a mismatch is a failure;
- anything else: wording code does not recognize may still allow another value, so a mismatch
  goes to review. An unknown sentence never becomes a pass or a failure on its own.
"""
from __future__ import annotations

import re

# A poster alone cannot settle these: a person decides even on an exact match.
_NEEDS_PERSON = re.compile(r"예외|제외|금지|다만|별도|증빙|담당자|취지")
# A human-added criterion may carry its allowance in the text instead of structured quotes.
_HUMAN_ALLOWANCE = re.compile(r"허용|예외|또는|줄바꿈|공백")
_FORMAT_WORDS = {"공백", "줄바꿈", "쉼표", "대소문자"}
_LABEL = re.compile(r"허용\s*범위\s*:")
_SENTENCE_BREAK = re.compile(r"[.\n]")
# Only recognized endings grant anything: '허용할 수 없다' or '허용 안됨' must never read as an allowance.
_GRANTED = r"(.+?)(?:은|는|을|를|도)?\s*허용"
_GRANT = re.compile(_GRANTED + r"(?:한다|합니다|함|된다|됩니다|됨|\s*가능(?:하다|합니다|함)?|할\s*수\s*있다)?")
_GRANT_AND_MORE = re.compile(_GRANTED + r"(?:하되|하고|하나|하지만|하며)\s+(.+)")
_ITEM_BREAK = re.compile(r"[·,/]|(?<=\S)[와과]\s|\s및\s|\s(?:또는|혹은)\s|(?<=\S)(?:이나|나)\s")
# A sentence that only holds the value as written cannot allow another value.
_KEEPS_VALUE = re.compile(r"(?:표기한다|표기해야\s*한다|유지한다|유지해야\s*한다|바꾸지\s*않는다|변경하지\s*않는다"
                          r"|누락하지\s*않는다|생략하지\s*않는다|남긴다|동일해야\s*한다)$")
_MAY_ALLOW_MORE = re.compile(r"또는|혹은|거나|도\s*되|달라도|로도|자유|가능|인정|무방|수\s*있|괜찮")


def qualifier_text(criterion) -> str:
    details = criterion.details
    if details is None:
        return ""
    quotes = [qualifier.quote for qualifier in details.qualifiers]
    if not quotes and criterion.human_added and _HUMAN_ALLOWANCE.search(criterion.text):
        quotes = [criterion.text]
    return "\n".join(quotes)


def needs_person(qualifier: str) -> bool:
    return bool(_NEEDS_PERSON.search(qualifier))


def _keeps_value(sentence: str) -> bool:
    return bool(_KEEPS_VALUE.search(sentence)) and not _MAY_ALLOW_MORE.search(sentence)


def _allowances(qualifier: str) -> tuple[set[str], bool]:
    """The format differences the source positively allows, and whether it may allow anything else."""
    allowed, other = set(), False
    for sentence in _SENTENCE_BREAK.split(_LABEL.sub(" ", qualifier)):
        sentence = sentence.strip()
        if not sentence:
            continue
        more = _GRANT_AND_MORE.fullmatch(sentence)
        grant = more or _GRANT.fullmatch(sentence)
        if grant is None:
            if "허용" in sentence:  # A refusal or unknown wording: compare exactly and let a person decide.
                return set(), True
            other = other or not _keeps_value(sentence)
            continue
        other = other or bool(more and not _keeps_value(more.group(2)))
        for item in _ITEM_BREAK.split(grant.group(1)):
            words = re.sub(r"\s*차이$", "", item.strip()).split()
            if words and words[-1].removesuffix("의") in _FORMAT_WORDS:
                allowed.add(words[-1].removesuffix("의"))
            elif words:
                other = True
    return allowed, other


def format_free(value: str, qualifier: str) -> str:
    """Remove only the differences the source positively allows; never interpret meaning."""
    allowed, _ = _allowances(qualifier)
    if "공백" in allowed:
        value = re.sub(r"\s+", "", value)
    elif "줄바꿈" in allowed:
        value = re.sub(r"\s*\n\s*", " ", value)
    if "쉼표" in allowed:
        value = value.replace(",", "")
    if "대소문자" in allowed:
        value = value.casefold()
    return value


def may_reject(qualifier: str) -> bool:
    """A mismatch is a confirmed failure only when the source allows nothing but format differences."""
    allowed, other = _allowances(qualifier)
    # A line break can split a word, which a line-break-only comparison cannot tell from a wrong value.
    return bool(allowed) and not other and ("공백" in allowed or "줄바꿈" not in allowed)
