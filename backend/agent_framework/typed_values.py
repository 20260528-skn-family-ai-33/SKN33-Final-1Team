"""Typed comparisons A06 can settle by code: dates, weekdays, years, clock times and uploaded-file facts.

Only notation is read, never meaning. A value that cannot be read with certainty is
UNKNOWN, which A06 keeps as review. A mismatch is reported only from values written
out in full: a year inferred from a range or printed apart from the day may confirm a
match, never a failure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re

MATCH, MISMATCH, UNKNOWN = "match", "mismatch", "unknown"
# Which date of an observed range a field means; a single-day field accepts no second date.
DATE_ROLES = {"start_date": "start", "end_date": "end", "event_date": "single"}

_MONTHS = {name: number for number, names in enumerate((
    ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
    ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
    ("dec", "december")), 1) for name in names}
_MONTH = "(?P<mon>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?(?![A-Za-z])"
_YEAR_DIGITS = r"(?:19|20)\d{2}"
_ORDINAL = r"(?:st|nd|rd|th)?"
# Tried in this order at each position. A day-first or two-digit-year date is never guessed.
_DATE_PATTERNS = tuple((kind, re.compile(pattern, re.I)) for kind, pattern in (
    ("full", rf"(?<![\d.])(?P<y>{_YEAR_DIGITS})\s*(?:년\s*|[./-]\s*)(?P<m>\d{{1,2}})\s*(?:월\s*|[./-]\s*)(?P<d>\d{{1,2}})(?:\s*일)?(?!\d)"),
    ("full", rf"(?<![A-Za-z]){_MONTH}\s*(?P<d>\d{{1,2}}){_ORDINAL}\s*,?\s*(?P<y>{_YEAR_DIGITS})(?!\d)"),
    ("full", rf"(?<!\d)(?P<d>\d{{1,2}}){_ORDINAL}\s+{_MONTH}\s*,?\s*(?P<y>{_YEAR_DIGITS})(?!\d)"),
    ("full", rf"(?<!\d)(?P<y>{_YEAR_DIGITS})\s*[.,]?\s*{_MONTH}\s*(?P<d>\d{{1,2}}){_ORDINAL}(?!\d)"),
    ("unclear", rf"(?<![\d.])\d{{1,2}}\s*[./-]\s*\d{{1,2}}\s*[./-]\s*{_YEAR_DIGITS}(?!\d)"),
    ("partial", r"(?<![\d.:])(?P<m>\d{1,2})\s*(?:월\s*|[./]\s*)(?P<d>\d{1,2})(?:\s*일)?(?![\d:])(?!\s*[./]\s*\d)"),
    ("partial", rf"(?<![A-Za-z]){_MONTH}\s*(?P<d>\d{{1,2}}){_ORDINAL}(?!\d)"),
    ("partial", rf"(?<!\d)(?P<d>\d{{1,2}}){_ORDINAL}\s+{_MONTH}"),
))
_BRACKETED = r"(?:[(\[（〔][^)\]）〕]{0,12}[)\]）〕]\s*)?"
_RANGE_MARK = "[~∼〜–—-]"
# 5.14-16: a day after a range mark continues the month just read. A floor or an hour is not a day.
_DAY_AFTER = re.compile(rf"\s*{_BRACKETED}{_RANGE_MARK}\s*(?P<d>\d{{1,2}})(?=\s*(?:일|$|[(\[（〔]|(?:mon|tue|wed|thu|fri|sat|sun)))", re.I)
# A range mark with no date after it means an end the text does not state. A time may follow a dash.
_OPEN_AFTER = re.compile(rf"\s*{_BRACKETED}(?:[A-Za-z]{{2,9}}\.?\s*)?{_RANGE_MARK}(?!\s*(?:(?:오전|오후|[ap]\.?m\.?)\s*)?\d{{1,2}}\s*(?::\s*\d|시))", re.I)
_OPEN_BEFORE = re.compile(r"[~∼〜]\s*$")
_STANDALONE_YEAR = re.compile(rf"(?<![\d.])({_YEAR_DIGITS})(?![\d.]\d|\d)")
_YEAR = re.compile(rf"(?<!\d)({_YEAR_DIGITS})(?!\d)")
_BARE_YEAR = re.compile(rf"\s*({_YEAR_DIGITS})\s*년?도?\s*")
# A range that runs into the next year is read as such only when it is short enough to be one event.
_MAX_YEAR_WRAP_DAYS = 200

_KO_WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_EN_WEEKDAYS = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2, "thu": 3,
                "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5, "sun": 6,
                "sunday": 6}
_EN_WEEKDAY = "(?<![A-Za-z])(?P<en>" + "|".join(sorted(_EN_WEEKDAYS, key=len, reverse=True)) + r")\.?(?![A-Za-z])"
# A single Korean letter counts as a weekday only inside brackets: 월 and 일 also end months and days.
_WEEKDAY = re.compile(r"(?P<ko>[월화수목금토일])요일|[(\[（〔]\s*(?P<short>[월화수목금토일])\s*[)\]）〕]|" + _EN_WEEKDAY, re.I)
_BARE_WEEKDAY = re.compile(r"\s*(?:(?P<ko>[월화수목금토일])요일|[(\[（〔]\s*(?P<short>[월화수목금토일])\s*[)\]）〕]|"
                           + _EN_WEEKDAY + r")\s*", re.I)

_CLOCK = re.compile(
    r"(?<![\d:.])(?P<h>\d{1,2})\s*:\s*(?P<m>[0-5]\d)(?!\d)"
    r"|(?<!\d)(?P<kh>\d{1,2})\s*시(?!간)(?:\s*(?:(?P<km>\d{1,2})\s*분|(?P<half>반)))?"
    r"|(?<![\d:.])(?P<bh>\d{1,2})(?=\s*[ap]\.?m\.?(?![A-Za-z]))", re.I)
_MERIDIEM_BEFORE = re.compile(r"(오전|오후|(?<![A-Za-z])[ap]\.?m\.?)\s*$", re.I)
_MERIDIEM_AFTER = re.compile(r"\s*([ap]\.?m\.?)(?![A-Za-z])", re.I)
# Words that move a clock time but are not read here; with them no time is compared by code.
_UNREAD_TIME_WORDS = re.compile(r"밤|저녁|낮|아침|새벽|정오|자정|noon|midnight", re.I)
_TIME_FILLER = re.compile(r"부터|까지|에서|(?<![A-Za-z])to(?![A-Za-z])|[\s~∼〜–—\-,·/()（）]+", re.I)

PIXEL_UNITS = frozenset({"px", "픽셀", "pixel", "pixels"})
# Print density is written either way in requirements; both name dots per inch of the file.
DPI_UNITS = frozenset({"dpi", "ppi"})
# KB, MB and GB are read both as powers of 1000 and of 1024; a size is settled only when both readings agree.
_BYTE_FACTORS = {"b": (1,), "byte": (1,), "bytes": (1,), "바이트": (1,),
                 "kb": (1000, 1024), "킬로바이트": (1000, 1024), "kib": (1024,),
                 "mb": (1000 ** 2, 1024 ** 2), "메가바이트": (1000 ** 2, 1024 ** 2), "mib": (1024 ** 2,),
                 "gb": (1000 ** 3, 1024 ** 3), "기가바이트": (1000 ** 3, 1024 ** 3), "gib": (1024 ** 3,)}
_COMPARE = {"equals": lambda seen, wanted: seen == wanted, "gte": lambda seen, wanted: seen >= wanted,
            "lte": lambda seen, wanted: seen <= wanted}
_UPLOAD_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})
_MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
# image/jpeg names the same format as JPEG; any other media type is left unread.
_MIME_PREFIX = re.compile(r"(?<![A-Za-z])image\s*/\s*(?=[A-Za-z])", re.I)
_FORMAT_NAMES = {"jpeg": "JPEG", "jpg": "JPEG", "png": "PNG", "webp": "WEBP", "pdf": "PDF", "tiff": "TIFF",
                 "tif": "TIFF", "gif": "GIF", "bmp": "BMP", "psd": "PSD", "ai": "AI", "eps": "EPS", "svg": "SVG",
                 "heic": "HEIC", "avif": "AVIF"}
_FORMAT_TOKEN = re.compile(r"(?<![A-Za-z])\.?(" + "|".join(sorted(_FORMAT_NAMES, key=len, reverse=True)) + r")(?![A-Za-z])", re.I)
_FORMAT_FILLER = re.compile(r"파일\s*형식|파일\s*포맷|납품\s*형식|확장자|형식|포맷|(?<![A-Za-z])(?:file\s*)?format(?![A-Za-z])"
                            r"|또는|혹은|(?<![A-Za-z])or(?![A-Za-z])|[\s:：()（）,·/|]+", re.I)
_COLOR_MODE = re.compile(r"\s*(RGB|CMYK)(?:\s*(?:색상|컬러|colou?r)?\s*(?:모드|mode))?\s*", re.I)
# Decoder mode names by colour model. A palette image (P) is not sorted: its entries may be anything.
_COLOR_FAMILIES = {"RGB": "RGB", "RGBA": "RGB", "RGBX": "RGB", "CMYK": "CMYK", "L": "GRAY", "LA": "GRAY", "1": "GRAY",
                   "I": "GRAY", "I;16": "GRAY", "F": "GRAY"}


@dataclass(frozen=True)
class _DateToken:
    kind: str                      # full, partial (month and day only) or unclear
    value: object
    start: int
    end: int


def _date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _date_tokens(text):
    tokens, position = [], 0
    while position < len(text):
        for kind, pattern in _DATE_PATTERNS:
            match = pattern.match(text, position)
            if match is None:
                continue
            if kind == "unclear":
                value = None
            else:
                groups = match.groupdict()
                month = _MONTHS[groups["mon"].lower()] if groups.get("mon") else int(groups["m"])
                day = int(groups["d"])
                # An impossible month or day is another kind of number, such as a time written 19.30.
                if kind == "full":
                    value = _date(int(groups["y"]), month, day)
                else:
                    value = (month, day) if _date(2024, month, day) is not None else None
                if value is None:
                    continue
            tokens.append(_DateToken(kind, value, match.start(), match.end()))
            position = match.end()
            following = _DAY_AFTER.match(text, position) if kind != "unclear" else None
            month = value.month if kind == "full" else value[0] if kind == "partial" else None
            if following is not None and _date(2024, month, int(following["d"])) is not None:
                tokens.append(_DateToken("partial", (month, int(following["d"])), following.start(), following.end()))
                position = following.end()
            break
        else:
            position += 1
    return tokens


def _outside(text, tokens):
    """The text with the given token spans blanked out."""
    characters = list(text)
    for token in tokens:
        characters[token.start:token.end] = " " * (token.end - token.start)
    return "".join(characters)


def _resolved_dates(text, tokens):
    """One (date, written in full) per token, or None where the year cannot be read."""
    years = set(_STANDALONE_YEAR.findall(_outside(text, tokens)))
    # A year printed apart from the day completes it only when no other year is in the text.
    composed = int(next(iter(years))) if len(years) == 1 and not any(t.kind == "full" for t in tokens) else None
    resolved, anchor = [], None
    for token in tokens:
        if token.kind == "full":
            anchor = token.value
            resolved.append((anchor, True))
            continue
        month, day = token.value
        if anchor is None:
            candidate = _date(composed, month, day) if composed is not None else None
        else:
            candidate = _date(anchor.year, month, day)
            if candidate is not None and candidate < anchor:
                candidate = _date(anchor.year + 1, month, day)
                if candidate is not None and (candidate - anchor).days > _MAX_YEAR_WRAP_DAYS:
                    candidate = None
        if candidate is None:
            resolved.append(None)
        else:
            anchor = candidate
            resolved.append((candidate, False))
    return resolved


def compare_date(expected: date, observed: str, role: str) -> str:
    tokens = _date_tokens(observed)
    if not tokens or any(token.kind == "unclear" for token in tokens):
        return UNKNOWN
    resolved = _resolved_dates(observed, tokens)
    dates = [item[0] for item in resolved if item is not None]
    # '~ 8.30' or '5.14 ~ 미정': one end of the range is not in the text at all.
    open_start = _OPEN_BEFORE.search(observed, 0, tokens[0].start) is not None
    open_end = _OPEN_AFTER.match(observed, tokens[-1].end) is not None
    closed = not open_start and not open_end and None not in resolved
    if role == "single":
        if closed and all(found == expected for found in dates):
            return MATCH
        only = resolved[0] if closed and len(resolved) == 1 else None
        return MISMATCH if only is not None and only[1] and only[0] != expected else UNKNOWN
    target = resolved[0] if role == "start" else resolved[-1]
    if target is not None and target[0] == expected and not (open_start if role == "start" else open_end):
        return MATCH
    # A list of dates may show the value in another place; only one date or one range is compared.
    if closed and target[1] and len(resolved) <= 2 and expected not in dates:
        return MISMATCH
    return UNKNOWN


def _stated_date(text, *, bare):
    """The one date written in full in a criterion's value; with bare, nothing else may be written."""
    tokens = _date_tokens(text)
    if len(tokens) != 1 or tokens[0].kind != "full":
        return None
    if bare and _outside(text, tokens).strip(" .\t\r\n"):
        return None
    return tokens[0].value


def _weekday(match):
    groups = match.groupdict()
    letter = groups.get("ko") or groups.get("short")
    return _KO_WEEKDAYS[letter] if letter else _EN_WEEKDAYS[groups["en"].lower()]


def _bare_weekday(text, field):
    match = _BARE_WEEKDAY.fullmatch(text)
    if match is not None:
        return _weekday(match)
    letter = text.strip()
    return _KO_WEEKDAYS[letter] if field == "weekday" and letter in _KO_WEEKDAYS else None


def compare_weekday(expected: int, observed: str) -> str:
    seen = {_weekday(match) for match in _WEEKDAY.finditer(observed)}
    if len(seen) != 1:
        return UNKNOWN
    return MATCH if expected in seen else MISMATCH


def compare_year(expected: int, observed: str) -> str:
    seen = {int(value) for value in _YEAR.findall(observed)}
    if len(seen) != 1:
        return UNKNOWN
    return MATCH if expected in seen else MISMATCH


@dataclass(frozen=True)
class _Clock:
    hour: int
    minute: int
    certain: bool                  # False: an hour from 1 to 11 written without morning or afternoon


def _clocks(text):
    """Clock times in reading order with the text they occupy blanked out, or None if they cannot be read."""
    if _UNREAD_TIME_WORDS.search(text):
        return None
    clocks, characters, used = [], list(text), 0
    for match in _CLOCK.finditer(text):
        groups = match.groupdict()
        hour = int(groups["h"] or groups["kh"] or groups["bh"])
        minute = 30 if groups["half"] else int(groups["m"] or groups["km"] or 0)
        # A mark written after one time is not also read as the mark before the next.
        before = _MERIDIEM_BEFORE.search(text, used, match.start())
        after = _MERIDIEM_AFTER.match(text, match.end())
        start, end = (before.start() if before else match.start()), (after.end() if after else match.end())
        used = end
        words = {(marker.group(1).lower().replace(".", "")) for marker in (before, after) if marker}
        meridiems = {"am" if word in ("오전", "am") else "pm" for word in words}
        if len(meridiems) > 1 or minute > 59 or hour > 24 or (hour == 24 and minute):
            return None
        meridiem = next(iter(meridiems), None)
        if meridiem == "pm":
            if hour == 0:
                return None
            hour = hour if hour >= 12 else hour + 12
        elif meridiem == "am":
            if hour > 12:
                return None
            hour = 0 if hour == 12 else hour
        clocks.append(_Clock(hour % 24, minute, meridiem is not None or hour == 0 or hour >= 12))
        characters[start:end] = " " * (end - start)
    return clocks, "".join(characters)


def _bare_clocks(text):
    read = _clocks(text)
    if read is None or not read[0] or _TIME_FILLER.sub("", read[1]):
        return None
    return read[0]


def _same_clock(expected, seen):
    """True, False, or None when only one side says morning or afternoon."""
    if expected.certain == seen.certain:
        return (expected.hour, expected.minute) == (seen.hour, seen.minute)
    if (expected.hour % 12, expected.minute) != (seen.hour % 12, seen.minute):
        return False
    return None


def compare_clocks(expected, observed: str) -> str:
    read = _clocks(observed)
    if read is None or not read[0]:
        return UNKNOWN
    seen = read[0]
    # The times must stand next to each other: 12시–19시 is not read out of a longer timetable.
    for start in range(len(seen) - len(expected) + 1):
        if all(_same_clock(wanted, found) is True for wanted, found in zip(expected, seen[start:start + len(expected)])):
            return MATCH
    # Extra times may belong to something else; only a one-to-one reading can differ.
    if len(seen) == len(expected):
        relations = [_same_clock(wanted, found) for wanted, found in zip(expected, seen)]
        if None not in relations and False in relations:
            return MISMATCH
    return UNKNOWN


def typed_kind(criterion) -> str | None:
    """The typed comparison a structured poster-text criterion takes, or None for plain wording."""
    details = criterion.details
    if (details is None or details.target != "poster_text" or criterion.kind != "text"
            or criterion.operator not in ("equals", "contains")):
        return None
    if details.field in DATE_ROLES:
        return "date"
    expected = criterion.expected
    if details.field == "year" and _BARE_YEAR.fullmatch(expected):
        return "year"
    if _bare_weekday(expected, details.field) is not None:
        return "weekday"
    if _stated_date(expected, bare=True) is not None:
        return "date"
    if _bare_clocks(expected) is not None:
        return "time"
    return None


def compare_typed(criterion, observed: str) -> str:
    """MATCH or MISMATCH when the observed text settles the typed value, otherwise UNKNOWN."""
    kind, details, expected = typed_kind(criterion), criterion.details, criterion.expected
    if kind == "date":
        role = DATE_ROLES.get(details.field, "single")
        wanted = None
        if details.normalized_expected is not None:
            try:
                wanted = date.fromisoformat(details.normalized_expected)
            except ValueError:
                return UNKNOWN
        else:
            # A date field may carry a weekday next to its date; any other field must be the date alone.
            wanted = _stated_date(expected, bare=details.field not in DATE_ROLES)
        return compare_date(wanted, observed, role) if wanted is not None else UNKNOWN
    if kind == "year":
        return compare_year(int(_BARE_YEAR.fullmatch(expected).group(1)), observed)
    if kind == "weekday":
        return compare_weekday(_bare_weekday(expected, details.field), observed)
    if kind == "time":
        return compare_clocks(_bare_clocks(expected), observed)
    return UNKNOWN


@dataclass(frozen=True)
class FileCheck:
    """A file-property criterion settled from the uploaded original, never from an inspector's answer."""
    outcome: str                   # MATCH or MISMATCH
    expected: str
    observed: str
    unit: str | None
    # False when the source adds wording code cannot read, so a difference is not a confirmed failure.
    rejects: bool


def _formats(text):
    """The file formats a value or source line lists, or None if it says anything else."""
    text = _MIME_PREFIX.sub("", text)
    names = {_FORMAT_NAMES[name.lower()] for name in _FORMAT_TOKEN.findall(text)}
    return names if names and not _FORMAT_FILLER.sub("", _FORMAT_TOKEN.sub("", text)) else None


def _plain(value: Decimal) -> str:
    """A decimal without exponent or trailing zeros: 300.00 is shown as 300."""
    return format(value.normalize(), "f")


def _measured_number(criterion, number):
    """The criterion's numeric value when code can compare it, else None."""
    if criterion.kind != "number" or criterion.operator not in _COMPARE:
        return None
    try:
        return number(criterion.expected, criterion.unit)[0]
    except (ValueError, InvalidOperation):
        return None


def file_check(criterion, original, qualifiers, number, notation_only) -> FileCheck | None:
    """Compare a file-property criterion with the measured original, or None when code cannot settle it.

    qualifiers are the criterion's allowance quotes, number parses one decimal with its unit and
    notation_only tells whether an allowance text grants nothing but notation differences.
    """
    details = criterion.details
    if original is None or details is None or details.target != "file_metadata":
        return None
    allowance = "\n".join(qualifiers)
    if details.field in ("pixel_width", "pixel_height"):
        unit = criterion.unit
        if (criterion.kind != "number" or criterion.operator not in ("equals", "gte", "lte")
                or unit is not None and unit.strip().casefold() not in PIXEL_UNITS):
            return None
        try:
            wanted, _unit = number(criterion.expected, unit)
        except (ValueError, InvalidOperation):
            return None
        measured = Decimal(original.width if details.field == "pixel_width" else original.height)
        matched = {"equals": measured == wanted, "gte": measured >= wanted, "lte": measured <= wanted}[criterion.operator]
        return FileCheck(MATCH if matched else MISMATCH, format(wanted, "f"), format(measured, "f"), unit,
                         notation_only(allowance))
    if details.field == "resolution_dpi":
        unit = criterion.unit
        # A file with no recorded density, or different densities per axis, is not read.
        if (original.dpi is None or original.dpi[0] != original.dpi[1]
                or unit is not None and unit.strip().casefold() not in DPI_UNITS):
            return None
        wanted = _measured_number(criterion, number)
        if wanted is None:
            return None
        measured = Decimal(str(original.dpi[0]))
        return FileCheck(MATCH if _COMPARE[criterion.operator](measured, wanted) else MISMATCH, _plain(wanted),
                         _plain(measured), unit, notation_only(allowance))
    if details.field == "file_size_bytes":
        factors = _BYTE_FACTORS.get((criterion.unit or "").strip().casefold())
        wanted = None if factors is None or original.byte_size is None else _measured_number(criterion, number)
        if wanted is None:
            return None
        outcomes = {_COMPARE[criterion.operator](Decimal(original.byte_size), wanted * factor) for factor in factors}
        if len(outcomes) != 1:
            return None
        # Shown in bytes: both readings of KB, MB or GB are listed, since the source does not say which it means.
        return FileCheck(MATCH if outcomes.pop() else MISMATCH, " / ".join(_plain(wanted * factor) for factor in factors),
                         str(original.byte_size), "B", notation_only(allowance))
    if criterion.kind != "text" or criterion.operator not in ("equals", "contains"):
        return None
    if details.field in ("file_format", "mime_type"):
        wanted = _formats(criterion.expected)
        if wanted is None:
            return None
        listed = [_formats(quote) for quote in qualifiers]
        # A source line that only lists formats names every accepted one; anything else stays unread.
        for names in listed:
            wanted |= names or set()
        if not wanted <= _UPLOAD_FORMATS:
            return None
        # A media type requirement is shown in media type spelling; the comparison is the same.
        shown = _MIME_TYPES if details.field == "mime_type" else {name: name for name in _UPLOAD_FORMATS}
        return FileCheck(MATCH if original.format in wanted else MISMATCH, " / ".join(sorted(shown[n] for n in wanted)),
                         shown[original.format], None, None not in listed)
    if details.field == "color_mode":
        stated = _COLOR_MODE.fullmatch(criterion.expected)
        family = _COLOR_FAMILIES.get(original.color_mode)
        if stated is None or family is None:
            return None
        wanted = stated.group(1).upper()
        return FileCheck(MATCH if family == wanted else MISMATCH, wanted, original.color_mode, None,
                         notation_only(allowance))
    return None
