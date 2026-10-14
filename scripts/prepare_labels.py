"""Create model-assisted reference labels for this single poster, never generic truth.

The expected input hash prevents accidentally labeling another poster with this text.
Coordinates are approximate, axis-aligned, original-image pixel boxes (right exclusive).
"""
import argparse
import hashlib
import json
import unicodedata
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from scripts.paths import DEFAULT_ARTIFACTS, PROJECT_ROOT, output_directory, source_reference, artifact_path

EXPECTED_SHA256 = "505650744b32f644c6820a817506a7d9e4ecc67c7468fc01fff840f4be050924"
REGIONS = [
    ("r01", "brand", "김천시", [70, 22, 130, 42], False),
    ("r02", "brand", "GIMCHEON CITY", [70, 40, 130, 52], False),
    ("r03", "event_year", "2026", [114, 65, 318, 126], True),
    ("r04", "slogan", "김밥특별시", [366, 76, 480, 104], False),
    ("r05", "unresolved_small_text", None, [351, 103, 485, 117], False),
    ("r06", "event_name", "김천김밥축제", [106, 119, 496, 211], True),
    ("r07", "event_date_range", "2026. 10. 23 금 ~ 10. 25 일", [134, 225, 475, 261], True),
    ("r08", "event_location", "김천시 직지문화공원, 사명대사공원, 친환경생태공원 일원", [102, 278, 501, 301], True),
    ("r09", "illustration_text", "김밥특별시", [271, 429, 356, 452], False),
    ("r10", "illustration_text", "방문을 환영합니다!", [259, 449, 365, 469], False),
    ("r11", "illustration_text", "I ♥ GC", [140, 574, 217, 607], False),
    ("r12", "unresolved_small_text", None, [416, 562, 451, 580], False),
]


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=DEFAULT_ARTIFACTS)
    ap.add_argument("--project", type=Path, default=PROJECT_ROOT)
    args = ap.parse_args()
    out = output_directory(args.output, args.input, args.project)
    source_hash = hashlib.sha256(args.input.read_bytes()).hexdigest()
    if source_hash != EXPECTED_SHA256:
        raise SystemExit("This reference annotation is only valid for the specified Gimcheon poster hash.")
    out.mkdir(parents=True, exist_ok=True)
    crops = artifact_path(out, "crops")
    crops.mkdir(exist_ok=True)
    with Image.open(args.input) as src:
        image = ImageOps.exif_transpose(src).convert("RGBA")
        white = Image.new("RGBA", image.size, "white")
        image = Image.alpha_composite(white, image).convert("RGB")
    assert image.size == (600, 848)
    image.save(artifact_path(out, "reference.png"))
    overlay = image.copy()
    draw = ImageDraw.Draw(overlay)
    annotations = []
    for region_id, role, raw, box, core in REGIONS:
        x1, y1, x2, y2 = box
        assert 0 <= x1 < x2 <= image.width and 0 <= y1 < y2 <= image.height
        color = "#e11d48" if core else "#2563eb" if raw else "#d97706"
        draw.rectangle([x1, y1, x2 - 1, y2 - 1], outline=color, width=2)
        draw.rectangle([max(0, x1 - 28), y1, x1, y1 + 14], fill=color)
        draw.text((max(0, x1 - 26), y1 + 1), region_id, fill="white")
        crop = image.crop(box)
        crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS).save(artifact_path(out, f"crops/{region_id}.png"))
        annotations.append({
            "id": region_id, "role": role, "text_raw": raw, "bbox_xyxy": box,
            "bbox_normalized_xyxy": [round(x1 / 600, 6), round(y1 / 848, 6), round(x2 / 600, 6), round(y2 / 848, 6)],
            "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
            "core_field_evidence": core, "use_for_training": False,
            "transcription_status": "model_assisted_draft" if raw else "unresolved_needs_human_review",
            "bbox_status": "approximate_region_including_outline" if region_id == "r06" else "approximate_region",
            "text_policy_note": "heart pictogram represented by ♥" if region_id == "r11" else None,
            "crop": f"crops/{region_id}.png",
        })
    overlay.save(artifact_path(out, "annotation_preview.png"))
    overlay.crop((0, 0, 600, 315)).save(artifact_path(out, "core_annotation_preview.png"))
    annotation = {
        "schema_version": "poster.reference.v1", "document_id": "gimcheon_gimbap_2026",
        "source": {**source_reference(args.input, args.project), "filename_original": args.input.name,
                   "filename_display_nfc": unicodedata.normalize("NFC", args.input.name),
                   "sha256": source_hash, "width": 600, "height": 848},
        "coordinate_system": {"origin": "top_left", "units": "original_image_pixels", "bbox_format": "xyxy", "right_bottom": "exclusive"},
        "provenance": {"method": "assistant_visual_transcription", "independent_agent_review": True,
                       "human_verified": False, "external_event_verified": False},
        "review_status": "needs_human_review", "annotation_scope": "core_event_fields_plus_selected_context_not_exhaustive_OCR",
        "notes": ["Not human ground truth; do not use for training or benchmark claims before approval.",
                  "Unresolved small English and mascot text intentionally remain null.",
                  "Logo/illustration text must not become organizer or venue evidence."],
        "regions": annotations,
    }
    write_json(artifact_path(out, "annotations.json"), annotation)
    fields = {
        "event_name": ("김천김밥축제", ["r06"], None),
        "display_title": ("2026 김천김밥축제", ["r03", "r06"], "Compose visible year and event name."),
        "start_date": ("2026-10-23", ["r07"], "Normalize dotted date to ISO 8601."),
        "end_date": ("2026-10-25", ["r07"], "Inherit 2026 from the same printed date range."),
        "start_weekday_raw": ("금", ["r07"], None),
        "end_weekday_raw": ("일", ["r07"], None),
        "location_raw": ("김천시 직지문화공원, 사명대사공원, 친환경생태공원 일원", ["r08"], None),
        "city_raw": ("김천시", ["r08"], "Use location line, not the top-left logo."),
        "venues": (["직지문화공원", "사명대사공원", "친환경생태공원"], ["r08"], "Split comma-separated venue list, preserving location scope separately."),
        "location_scope": ("일원", ["r08"], None),
    }
    event = {"schema_version": "poster.event.v1", "document_id": annotation["document_id"],
             "source_sha256": source_hash, "method": "assistant_visual_reference_normalization",
             "review_status": "needs_human_review", "human_verified": False, "fields": {}}
    for key, (value, evidence, note) in fields.items():
        event["fields"][key] = {"value": value, "source_region_ids": evidence,
                                "normalization_note": note, "status": "model_assisted_draft"}
    for key in ["organizer", "opening_hours", "admission_fee", "contact", "website", "street_address", "latitude", "longitude"]:
        event["fields"][key] = {"value": None, "source_region_ids": [], "normalization_note": None, "status": "not_visible_in_poster"}
    event["checks"] = {"start_before_end": date(2026, 10, 23) <= date(2026, 10, 25),
                         "printed_start_weekday_matches_calendar": date(2026, 10, 23).weekday() == 4,
                         "printed_end_weekday_matches_calendar": date(2026, 10, 25).weekday() == 6}
    assert all(event["checks"].values())
    write_json(artifact_path(out, "event_reference.json"), event)
    print(json.dumps({"source_sha256": source_hash, "regions": len(annotations), "status": "needs_human_review", "output": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
