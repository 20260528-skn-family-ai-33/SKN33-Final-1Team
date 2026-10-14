#!/usr/bin/env python3
"""Conservative event-field extraction from line-level OCR evidence.

This is a small rule parser for a sample workflow, not a general event model.
It reads only the OCR records supplied by the caller; it never reads labels,
filenames, a reference answer, or external event information. It does not join
separate OCR lines or correct OCR characters. Titles must end in 축제/페스티벌;
locations must contain 공원/일원; numerical date ranges must share one line.

Each field contains source_ocr_ids. A non-null value means a rule matched, not
that the content is true. OCR confidence is not a correctness probability and
is neither averaged into a field score nor used to declare a value correct.
Missing, conflicting, invalid, and unsupported values require human review.

Usage:
  python -m backend.app.extract_event --input ocr.json --output event.json
The input may be a records list or an object containing a "records" list.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
import math
from pathlib import Path
import re
import sys
import unicodedata


WEEKDAYS = "월화수목금토일"
DAY_SEPARATOR = r"\s*[./-]\s*"
WEEKDAY = r"\s*(?:[\(（\[]?\s*([월화수목금토일])(?:요일)?\s*[\)）\]]?)?"
DATE_RANGE = re.compile(
    r"(?<!\d)(\d{4})" + DAY_SEPARATOR + r"(\d{1,2})"
    + DAY_SEPARATOR + r"(\d{1,2})(?!\d)\s*\.?" + WEEKDAY
    + r"\s*[~∼〜～–—]\s*"
    + r"(?:(\d{4})" + DAY_SEPARATOR + r")?(\d{1,2})"
    + DAY_SEPARATOR + r"(\d{1,2})(?!\d)\s*\.?" + WEEKDAY
)
DATE_HINT = re.compile(r"(?<!\d)\d{4}\s*[./-]\s*\d{1,2}")
TITLE_END = re.compile(r"(?:축제|페스티벌)$")
YEAR_PREFIX = re.compile(r"^\d{4}(?!\d)\s*(?:년\s*)?")
LOCATION_HINT = re.compile(r"공원|일원")
OPTIONAL_HINTS = {
    "organizer": re.compile(r"주최|주관"),
    "opening_hours": re.compile(r"운영\s*시간|개장\s*시간|\d{1,2}\s*:\s*\d{2}"),
    "admission_fee": re.compile(r"입장료|참가비|요금|무료|유료"),
    "contact": re.compile(r"문의|전화|(?<!\d)0\d{1,2}[- )]\d{3,4}-\d{4}(?!\d)"),
    "website": re.compile(r"https?://|www\.", re.IGNORECASE),
}


def _field(value=None, ids=None, normalization=None, reason=None) -> dict:
    field = {
        "value": value,
        "source_ocr_ids": list(dict.fromkeys(ids or [])),
        "status": "extracted" if value is not None else "needs_review",
        "normalization": normalization or [],
    }
    if reason:
        field["reason"] = reason
    return field


def _warning(code: str, message: str, ids=None) -> dict:
    return {
        "code": code,
        "message": message,
        "source_ocr_ids": list(dict.fromkeys(ids or [])),
    }


def _validate_records(records: list) -> None:
    if not isinstance(records, list):
        raise ValueError("OCR records must be a list")
    seen = set()
    for position, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"Record {position} must be an object")
        record_id = record.get("id")
        if isinstance(record_id, bool) or not isinstance(record_id, (str, int)) or record_id == "":
            raise ValueError(f"Record {position} needs a non-empty string or integer id")
        if record_id in seen:
            raise ValueError(f"Duplicate record id: {record_id}")
        seen.add(record_id)
        if not isinstance(record.get("text"), str):
            raise ValueError(f"Record {record_id} needs text as a string")
        box = record.get("bbox_xyxy")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise ValueError(f"Record {record_id} needs bbox_xyxy with four coordinates")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in box):
            raise ValueError(f"Record {record_id} has non-finite/non-numeric bbox coordinates")
        if box[0] < 0 or box[1] < 0 or box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError(f"Record {record_id} has a negative or degenerate bounding box")
        confidence = record.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError(f"Record {record_id} confidence must be null or a finite number in [0, 1]")


def _select_unique(candidates: list[tuple[str, str | int]], field_name: str, normalization: list, warnings: list) -> dict:
    values = {}
    for value, record_id in candidates:
        values.setdefault(value, []).append(record_id)
    if len(values) == 1:
        value, ids = next(iter(values.items()))
        return _field(value, ids, normalization)
    ids = [record_id for _, record_id in candidates]
    reason = "No matching OCR line" if not values else "Multiple distinct OCR candidates"
    warnings.append(_warning(f"{field_name}_needs_review", reason, ids))
    return _field(ids=ids, normalization=normalization, reason=reason)


def _parse_date_match(match: re.Match) -> tuple[date, date]:
    year, month, day, weekday, end_year, end_month, end_day, end_weekday = match.groups()
    start = date(int(year), int(month), int(day))
    end = date(int(end_year or year), int(end_month), int(end_day))
    if end < start:
        if end_year is None:
            raise ValueError("End precedes start with omitted end year; year rollover must be reviewed")
        raise ValueError("Explicit end date precedes start date")
    for value, observed_weekday in [(start, weekday), (end, end_weekday)]:
        if observed_weekday is not None and WEEKDAYS[value.weekday()] != observed_weekday:
            raise ValueError(f"Observed weekday disagrees with calendar date {value.isoformat()}")
    return start, end


def extract_event(records: list[dict]) -> dict:
    """Return evidence-linked fields from OCR records in original-image coordinates.

    Input IDs are preserved. Bounding boxes are validated but are not used to
    guess reading order or combine lines. Absence from OCR does not establish
    absence from the image. Optional fields currently remain null even if a
    keyword is observed, with that line linked for manual review.
    """
    _validate_records(records)
    warnings = []
    titles = []
    locations = []
    valid_dates = []
    date_source_ids = []
    date_errors = []
    optional_ids = {name: [] for name in OPTIONAL_HINTS}

    for record in records:
        record_id = record["id"]
        raw_text = record["text"]
        text = unicodedata.normalize("NFC", raw_text).strip()
        if TITLE_END.search(text):
            title = YEAR_PREFIX.sub("", text, count=1)
            title = re.sub(r"\s+", "", title)
            if title:
                titles.append((title, record_id))
        if LOCATION_HINT.search(text):
            # Preserve the OCR string verbatim: no guessed address expansion.
            locations.append((raw_text, record_id))

        matches = list(DATE_RANGE.finditer(text))
        if matches:
            date_source_ids.append(record_id)
            for match in matches:
                try:
                    start, end = _parse_date_match(match)
                    valid_dates.append(((start.isoformat(), end.isoformat()), record_id))
                except ValueError as error:
                    date_errors.append(str(error))
        elif DATE_HINT.search(text):
            date_source_ids.append(record_id)
            date_errors.append("Date-like OCR line does not match a complete supported date range")
        for name, pattern in OPTIONAL_HINTS.items():
            if pattern.search(text):
                optional_ids[name].append(record_id)

    fields = {
        "event_name": _select_unique(
            titles, "event_name",
            ["Unicode NFC", "trim surrounding whitespace", "remove a leading four-digit year and optional 년", "remove internal whitespace"],
            warnings,
        ),
        "location_raw": _select_unique(locations, "location_raw", [], warnings),
    }
    date_pairs = {}
    for pair, record_id in valid_dates:
        date_pairs.setdefault(pair, []).append(record_id)
    date_normalization = [
        "Parse a numerical year.month.day ~ month.day range on one OCR line",
        "Use an explicit end year when provided; otherwise use the observed start year only if end is not earlier",
        "Validate calendar dates and any observed Korean weekday",
        "Convert to ISO 8601 YYYY-MM-DD",
    ]
    if len(date_pairs) == 1 and not date_errors:
        pair, ids = next(iter(date_pairs.items()))
        fields["start_date"] = _field(pair[0], ids, date_normalization)
        fields["end_date"] = _field(pair[1], ids, date_normalization)
    else:
        reason = "; ".join(dict.fromkeys(date_errors)) if date_errors else (
            "Multiple distinct OCR date ranges" if date_pairs else "No complete supported OCR date range"
        )
        for name in ["start_date", "end_date"]:
            fields[name] = _field(ids=date_source_ids, normalization=date_normalization, reason=reason)
        warnings.append(_warning("date_range_needs_review", reason, date_source_ids))

    for name, ids in optional_ids.items():
        reason = (
            "Keyword observed, but extraction of this optional field is outside this parser's scope"
            if ids else "No matching OCR evidence; this does not establish that the poster omits this field"
        )
        fields[name] = _field(ids=ids, reason=reason)
        if ids:
            warnings.append(_warning(f"{name}_requires_manual_extraction", reason, ids))

    warnings.append(_warning(
        "rule_match_is_not_verified_truth",
        "Extracted values are unverified OCR-based rule matches. OCR confidence is not a correctness probability; review against the image before use.",
    ))
    return {
        "method": "conservative_line_rules_v1",
        "fields": fields,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, type=Path, help="OCR JSON list or object with records")
    parser.add_argument("--output", type=Path, help="JSON destination; default is standard output")
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        records = payload.get("records") if isinstance(payload, dict) else payload
        result = extract_event(records)
        serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            if args.output.resolve() == args.input.resolve():
                raise ValueError("Output must differ from input OCR JSON")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
            print(str(args.output.resolve()))
        else:
            sys.stdout.write(serialized)
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
