"""Compare real full-poster OCR against an explicitly unapproved visual draft.

Reference regions are used only for evaluation; automatic extraction receives
only OCR records, never the reference text or regions. CER is a sample diagnostic.
"""
import argparse
import json
import re
import unicodedata
from pathlib import Path

from backend.app.extract_event import extract_event
from scripts.paths import DEFAULT_ARTIFACTS, PROJECT_ROOT, output_directory, artifact_path


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text or ""))


def distance(a, b):
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        new = [i]
        for j, y in enumerate(b, 1):
            new.append(min(new[-1] + 1, row[j] + 1, row[j - 1] + (x != y)))
        row = new
    return row[-1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    ap.add_argument("--project", type=Path, default=PROJECT_ROOT)
    args = ap.parse_args()
    art = output_directory(args.artifacts, project=args.project)
    annotation = json.loads((art / "annotations.json").read_text(encoding="utf-8"))
    reference = json.loads((art / "event_reference.json").read_text(encoding="utf-8"))
    core_regions = [r for r in annotation["regions"] if r["core_field_evidence"]]
    variants, events = [], {}
    for path in sorted((art / "ocr").glob("*.json")):
        if ".raw." in path.name or "raw" in path.stem:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "records" not in payload:
            continue
        if payload.get("engine") != "easyocr":
            raise ValueError(f"Unsupported OCR engine in {path.name}")
        if payload.get("source_sha256") != annotation["source"]["sha256"]:
            raise ValueError(f"Source hash mismatch in {path.name}")
        variant = payload.get("variant", path.stem)
        status = payload.get("status", "unknown")
        row = {"variant": variant, "status": status, "ocr_file": f"ocr/{path.name}",
               "core_cer": None, "exact_fields": None, "event_name": None}
        if status not in ["success", "ok", "completed"]:
            row["error"] = payload.get("error")
            variants.append(row)
            continue
        records = payload["records"]
        for r in records:
            box = r["bbox_xyxy"]
            assert 0 <= box[0] < box[2] <= 600.001 and 0 <= box[1] < box[3] <= 848.001, box
        event = extract_event(records)
        event["ocr_file"] = row["ocr_file"]
        events[variant] = event
        edits, reference_chars, region_comparisons = 0, 0, []
        for region in core_regions:
            x1, y1, x2, y2 = region["bbox_xyxy"]
            matched = [r for r in records if x1 <= (r["bbox_xyxy"][0] + r["bbox_xyxy"][2]) / 2 < x2
                       and y1 <= (r["bbox_xyxy"][1] + r["bbox_xyxy"][3]) / 2 < y2]
            matched.sort(key=lambda r: (round((r["bbox_xyxy"][1] + r["bbox_xyxy"][3]) / 24), r["bbox_xyxy"][0]))
            observed = " ".join(r["text"] for r in matched)
            expected, actual = compact(region["text_raw"]), compact(observed)
            d = distance(expected, actual)
            edits += d
            reference_chars += len(expected)
            region_comparisons.append({"region_id": region["id"], "reference": region["text_raw"],
                                       "ocr_text": observed, "ocr_ids": [r["id"] for r in matched],
                                       "edit_distance": d, "reference_characters": len(expected)})
        field_comparisons = {}
        for key in ["event_name", "start_date", "end_date", "location_raw"]:
            actual = event["fields"][key]["value"]
            expected = reference["fields"][key]["value"]
            field_comparisons[key] = {"actual": actual, "reference": expected,
                                      "exact_match_ignoring_whitespace": actual is not None and compact(actual) == compact(expected)}
        row.update(core_cer=edits / reference_chars, edit_distance=edits, reference_characters=reference_chars,
                   exact_fields=sum(f["exact_match_ignoring_whitespace"] for f in field_comparisons.values()),
                   event_name=event["fields"]["event_name"]["value"],
                   region_comparisons=region_comparisons, field_comparisons=field_comparisons,
                   detected_text_count=len(records))
        variants.append(row)
    successes = [v for v in variants if v["core_cer"] is not None]
    simplicity_order = {"original": 0, "rgb_2x": 1, "gray_2x": 2, "clahe_2x": 3, "threshold_2x": 4}
    best = min(successes, key=lambda v: (-v["exact_fields"], v["core_cer"], simplicity_order.get(v["variant"], 99))) if successes else None
    tied = [v["variant"] for v in successes if best and v["exact_fields"] == best["exact_fields"] and v["core_cer"] == best["core_cer"]]
    selected = "rgb_2x" if "rgb_2x" in events else next(iter(events), None)
    auto = {"schema_version": "poster.auto.v1", "source_sha256": annotation["source"]["sha256"],
            "human_verified": False, "selected_variant": selected,
            "selection_policy": "Fixed rgb_2x baseline when available; otherwise first successful variant in filename order. Reference scores are not used to populate fields.",
            "selected_event": events.get(selected), "variants": events}
    write(artifact_path(art, "event_auto.json"), auto)
    summary = (f"실제 OCR {len(successes)}개 입력 비교 완료. 최상위 입력은 {', '.join(tied)}이며 핵심 필드 {best['exact_fields']}/4가 이 초안과 일치합니다. 기본 자동 결과는 {selected}입니다."
               if best else "성공한 자동 OCR 결과가 없습니다. 전처리 이력의 오류를 확인하세요. 시각 판독 라벨은 자동 OCR 결과가 아닙니다.")
    comparison = {"schema_version": "poster.comparison.v1", "reference_status": "model_assisted_draft_not_ground_truth",
                  "summary_ko": summary, "variants": variants, "best_on_this_sample": best["variant"] if best else None,
                  "tied_best_variants": tied,
                  "metric_notes": [
                      "CER = sum Levenshtein edits / sum reference characters over r03,r06,r07,r08.",
                      "Unicode NFC and all-whitespace removal only; punctuation retained. CER can exceed 1 with insertions.",
                      "OCR boxes assigned to regions by centroid; approximate boxes and line segmentation affect scores.",
                      "Field agreement uses event_name, start_date, end_date, location_raw, ignoring whitespace.",
                      "Not a held-out benchmark: reference is an unapproved assistant draft and only one poster is measured.",
                      "The best variant is selected against this sample for diagnosis only; it does not establish a general preprocessing default."]}
    write(artifact_path(art, "comparison.json"), comparison)
    print(summary)


if __name__ == "__main__":
    main()
