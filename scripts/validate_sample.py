"""Validate this poster's real artifacts; a pass never certifies human labels.

Only validation.json is written. The source and all inspected artifacts are read-only.
"""
import argparse
import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps

from scripts.paths import DEFAULT_ARTIFACTS, PROJECT_ROOT, output_directory, resolve_source, artifact_path


EXPECTED_SOURCE_SHA256 = "505650744b32f644c6820a817506a7d9e4ecc67c7468fc01fff840f4be050924"
MISSING_FIELDS = (
    "organizer", "opening_hours", "admission_fee", "contact", "website",
    "street_address", "latitude", "longitude",
)
WEEKDAYS_KO = "월화수목금토일"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    parser.add_argument("--project", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--input", type=Path, help="Required for an external source reference")
    args = parser.parse_args()
    out = output_directory(args.artifacts, args.input, args.project)
    if not out.is_dir():
        parser.error(f"Artifact directory does not exist: {out}")
    checks = []
    inspected_images = []
    source_path = None
    source_hash_before = None

    def check(name, passed, **detail):
        checks.append({"check": name, "passed": bool(passed), **detail})
        return bool(passed)

    def load_image(path, name):
        try:
            with Image.open(path) as image:
                image.load()  # Decode pixels, not just the file header.
                info = {"path": path.relative_to(out).as_posix() if path.is_relative_to(out) else "source_image", "format": image.format,
                        "mode": image.mode, "size": list(image.size)}
                if "A" in image.getbands():
                    info["alpha_extrema"] = list(image.getchannel("A").getextrema())
                inspected_images.append(info)
                decoded = image.copy()
            check(f"{name}.decodes", True)
            return decoded
        except Exception as exc:
            check(f"{name}.decodes", False, error=f"{type(exc).__name__}: {exc}")
            return None

    def safe_artifact(relative):
        path = (out / relative).resolve()
        if not path.is_relative_to(out):
            raise ValueError(f"Artifact path escapes its directory: {relative}")
        return path

    try:
        annotation = json.loads((out / "annotations.json").read_text(encoding="utf-8"))
        event = json.loads((out / "event_reference.json").read_text(encoding="utf-8"))
        source = annotation["source"]
        source_path = resolve_source(source, args.project, args.input)
        output_directory(out, source_path, args.project)
        source_hash_before = sha256(source_path)
        check("source.matches_fixed_sample_hash", source_hash_before == EXPECTED_SOURCE_SHA256,
              actual=source_hash_before, expected=EXPECTED_SOURCE_SHA256)
        check("source.matches_annotation_hash", source_hash_before == source["sha256"])
        check("source.matches_event_hash", source_hash_before == event["source_sha256"])
        check("document_ids_match", annotation["document_id"] == event["document_id"])
        source_image = load_image(source_path, "source")
        width, height = source["width"], source["height"]
        check("source.annotated_dimensions", [width, height] == [600, 848])
        if source_image is not None:
            check("source.decoded_dimensions", source_image.size == (width, height),
                  actual=list(source_image.size), expected=[width, height])
        check("coordinates.original_xyxy_exclusive", annotation["coordinate_system"] == {
            "origin": "top_left", "units": "original_image_pixels",
            "bbox_format": "xyxy", "right_bottom": "exclusive",
        })
        check("annotation.not_human_verified", annotation["provenance"].get("human_verified") is False)
        check("event.not_human_verified", event.get("human_verified") is False)
        check("annotation.requires_human_review", annotation.get("review_status") == "needs_human_review")
        check("event.requires_human_review", event.get("review_status") == "needs_human_review")

        reference = load_image(out / "reference.png", "reference")
        preview = load_image(out / "annotation_preview.png", "annotation_preview")
        for name, image in (("reference", reference), ("annotation_preview", preview)):
            if image is not None:
                check(f"{name}.dimensions", image.size == (width, height), actual=list(image.size))
        if source_image is not None and reference is not None:
            oriented = ImageOps.exif_transpose(source_image).convert("RGBA")
            flattened = Image.alpha_composite(Image.new("RGBA", oriented.size, "white"), oriented).convert("RGB")
            check("reference.matches_source_pixels", reference.convert("RGB").tobytes() == flattened.tobytes()
                  and reference.size == flattened.size)

        regions = annotation["regions"]
        region_ids = [region["id"] for region in regions]
        check("regions.nonempty_unique_ids", bool(region_ids) and len(region_ids) == len(set(region_ids)))
        regions_by_id = {region["id"]: region for region in regions}
        for region in regions:
            region_id = region["id"]
            prefix = f"regions.{region_id}"
            check(f"{prefix}.not_training_truth", region.get("use_for_training") is False)
            check(f"{prefix}.draft_status", region.get("transcription_status") in {
                "model_assisted_draft", "unresolved_needs_human_review",
            })
            if region.get("role") == "unresolved_small_text":
                check(f"{prefix}.unresolved_is_null", region.get("text_raw") is None)
            box = region.get("bbox_xyxy", [])
            valid_numbers = len(box) == 4 and all(type(value) is int for value in box)
            bounds_ok = valid_numbers and 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height
            check(f"{prefix}.bbox_bounds", bounds_ok, actual=box)
            normalized = region.get("bbox_normalized_xyxy", [])
            normalized_valid = len(normalized) == 4 and all(
                type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
                for value in normalized
            )
            if bounds_ok and normalized_valid:
                error = max(abs(norm * scale - pixel) for norm, scale, pixel in
                            zip(normalized, (width, height, width, height), box))
                check(f"{prefix}.normalized_coordinates", error <= 1e-3,
                      max_original_pixel_error=error, tolerance_original_pixels=1e-3)
                x1, y1, x2, y2 = box
                check(f"{prefix}.polygon_matches_bbox", region.get("polygon") == [
                    [x1, y1], [x2, y1], [x2, y2], [x1, y2],
                ])
            else:
                check(f"{prefix}.normalized_coordinates", False, error="Invalid bbox or normalized values")
            crop = load_image(safe_artifact(region["crop"]), f"{prefix}.crop")
            if crop is not None and bounds_ok:
                expected_size = ((box[2] - box[0]) * 2, (box[3] - box[1]) * 2)
                check(f"{prefix}.crop_dimensions", crop.size == expected_size,
                      actual=list(crop.size), expected=list(expected_size), scale=2)
                if reference is not None:
                    expected_crop = reference.crop(box).resize(expected_size, Image.Resampling.LANCZOS)
                    check(f"{prefix}.crop_matches_reference_pixels", crop.size == expected_size
                          and crop.convert("RGB").tobytes() == expected_crop.convert("RGB").tobytes())

        fields = event["fields"]
        for name, field in fields.items():
            evidence = field.get("source_region_ids")
            valid_evidence = isinstance(evidence, list) and all(
                isinstance(region_id, str) and region_id in regions_by_id for region_id in evidence
            )
            check(f"fields.{name}.evidence_exists", valid_evidence)
            if field.get("value") is not None:
                check(f"fields.{name}.nonempty_evidence", valid_evidence and bool(evidence))
                if valid_evidence:
                    check(f"fields.{name}.evidence_is_core", all(
                        regions_by_id[region_id].get("core_field_evidence") is True for region_id in evidence
                    ))
            if field.get("status") == "not_visible_in_poster":
                check(f"fields.{name}.unseen_value_null", "value" in field and field["value"] is None)
        for name in MISSING_FIELDS:
            field = fields.get(name, {})
            check(f"missing_fields.{name}.null_without_evidence", "value" in field and field["value"] is None
                  and field.get("source_region_ids") == [] and field.get("status") == "not_visible_in_poster")

        try:
            start_text, end_text = fields["start_date"]["value"], fields["end_date"]["value"]
            iso_ok = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_text)) and bool(
                re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_text)
            )
            check("dates.iso_format", iso_ok)
            start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
            computed_checks = {
                "start_before_end": start <= end,
                "printed_start_weekday_matches_calendar": fields["start_weekday_raw"]["value"] == WEEKDAYS_KO[start.weekday()],
                "printed_end_weekday_matches_calendar": fields["end_weekday_raw"]["value"] == WEEKDAYS_KO[end.weekday()],
            }
            for name, passed in computed_checks.items():
                check(f"dates.{name}", passed)
                check(f"dates.recorded_{name}_accurate", event.get("checks", {}).get(name) is passed)
            date_evidence = fields["start_date"]["source_region_ids"]
            range_text = " ".join(regions_by_id[region_id]["text_raw"] for region_id in date_evidence)
            match = re.fullmatch(r"\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\s*([월화수목금토일])\s*~\s*(\d{1,2})\.\s*(\d{1,2})\s*([월화수목금토일])\s*", range_text)
            check("dates.printed_range_parseable", match is not None)
            if match:
                year, sm, sd, sw, em, ed, ew = match.groups()
                check("dates.matches_printed_range", start == date(int(year), int(sm), int(sd))
                      and end == date(int(year), int(em), int(ed))
                      and sw == fields["start_weekday_raw"]["value"] and ew == fields["end_weekday_raw"]["value"])
        except Exception as exc:
            check("dates.valid", False, error=f"{type(exc).__name__}: {exc}")

        # Optional preprocessing outputs can appear after the label artifacts.
        for path in sorted((out / "images").glob("*.png")):
            name = f"preprocessing.{path.name}"
            image = load_image(path, name)
            if image is not None:
                check(f"{name}.dimensions", image.size in {(width, height), (width * 2, height * 2)},
                      actual=list(image.size), allowed=[[width, height], [width * 2, height * 2]])
    except Exception as exc:
        check("validation.completed_without_structural_error", False, error=f"{type(exc).__name__}: {exc}")
    finally:
        if source_path is not None and source_hash_before is not None:
            try:
                check("source.unchanged_during_validation", sha256(source_path) == source_hash_before)
            except Exception as exc:
                check("source.unchanged_during_validation", False, error=f"{type(exc).__name__}: {exc}")

    failures = [item for item in checks if not item["passed"]]
    report = {
        "schema_version": "poster.validation.v1",
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if not failures else "failed",
        "human_verified": False,
        "semantic_review_status": "needs_human_review",
        "scope": "Artifact integrity, geometry, provenance and date consistency; not human or external event verification.",
        "artifacts_directory": ".",
        "source_sha256": source_hash_before,
        "summary": {"checks": len(checks), "passed": len(checks) - len(failures),
                    "failed": len(failures), "images_decoded": len(inspected_images)},
        "images": inspected_images,
        "checks": checks,
    }
    report_path = artifact_path(out, "validation.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], **report["summary"], "report": str(report_path)}, ensure_ascii=False))
    for item in failures:
        print(json.dumps(item, ensure_ascii=False))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
