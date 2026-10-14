#!/usr/bin/env python3
"""Inventory immediate poster files in PROJECT/data/img without changing them.

Requires Python 3.10+ and Pillow. Example:
  python -m scripts.inventory --project /path/to/project --output artifacts/project_inventory.json

Filename extensions are compared with file signatures. A PDF-compatible .ai is
reported as an AI original with PDF signature, not as an extension mismatch.
Relative filesystem paths are retained; NFC is used only for display names.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import unicodedata

from PIL import Image, UnidentifiedImageError

from scripts.paths import PROJECT_ROOT, output_directory


RASTER_FORMATS = {"png", "jpeg", "gif", "tiff", "bmp", "webp"}
EXPECTED_EXTENSIONS = {
    "png": {".png"},
    "jpeg": {".jpg", ".jpeg", ".jpe"},
    "gif": {".gif"},
    "tiff": {".tif", ".tiff"},
    "bmp": {".bmp"},
    "webp": {".webp"},
    "pdf": {".pdf", ".ai"},
    "postscript": {".ps", ".eps", ".ai"},
}


def detect_format(header: bytes) -> str:
    """Return a format from the file's leading bytes, never its extension."""
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if header.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")):
        return "tiff"
    if header.startswith(b"BM"):
        return "bmp"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "webp"
    if header.startswith(b"%PDF-"):
        return "pdf"
    if header.startswith(b"%!PS-Adobe"):
        return "postscript"
    return "unknown"


def inspect_file(path: Path, project: Path) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        header = stream.read(32)
        digest.update(header)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    actual_format = detect_format(header)
    extension = path.suffix.lower()
    allowed = EXPECTED_EXTENSIONS.get(actual_format)
    pdf_compatible_ai = extension == ".ai" and actual_format == "pdf"
    record = {
        "relative_path": path.relative_to(project).as_posix(),
        "display_filename": unicodedata.normalize("NFC", path.name),
        "filename_is_nfc": path.name == unicodedata.normalize("NFC", path.name),
        "extension": extension,
        "actual_format": actual_format,
        "format_detection": "file_signature",
        "format_description": (
            "AI original with PDF signature" if pdf_compatible_ai else actual_format.upper()
        ),
        "extension_mismatch": None if allowed is None else extension not in allowed,
        "bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
        "raster": None,
    }
    if actual_format in RASTER_FORMATS:
        try:
            with Image.open(path) as image:
                record["raster"] = {
                    "width": image.width,
                    "height": image.height,
                    "mode": image.mode,
                    "has_alpha_channel": "A" in image.getbands(),
                    "has_transparency_metadata": "transparency" in image.info,
                    "pillow_format": image.format,
                }
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
            record["raster_error"] = f"{type(error).__name__}: {error}"
    return record


def inventory(project: Path) -> dict:
    project = project.expanduser().resolve(strict=True)
    source = project / "data" / "img"
    if not source.is_dir():
        raise ValueError(f"Poster directory does not exist: {source}")
    if source.is_symlink() or not source.resolve().is_relative_to(project):
        raise ValueError("data/img must be a real directory inside the project")

    records = []
    skipped = []
    for path in sorted(source.iterdir(), key=lambda item: (unicodedata.normalize("NFC", item.name), item.name)):
        if path.name.startswith("."):
            skipped.append({"relative_path": path.relative_to(project).as_posix(), "reason": "hidden_file_or_directory"})
        elif path.is_symlink():
            skipped.append({"relative_path": path.relative_to(project).as_posix(), "reason": "symbolic_link"})
        elif not path.is_file():
            skipped.append({"relative_path": path.relative_to(project).as_posix(), "reason": "not_a_regular_file"})
        else:
            records.append(inspect_file(path, project))

    by_hash = defaultdict(list)
    for item in records:
        by_hash[item["sha256"]].append(item["relative_path"])
    duplicates = [
        {"sha256": digest, "relative_paths": paths, "count": len(paths)}
        for digest, paths in sorted(by_hash.items()) if len(paths) > 1
    ]
    mismatches = [item["relative_path"] for item in records if item["extension_mismatch"] is True]
    return {
        "schema_version": "1.0",
        "project_root": ".",
        "source_directory": "data/img",
        "scope": "Immediate non-hidden regular files only; symlinks are skipped. Source files are read only.",
        "notes": [
            "relative_path preserves the original filesystem name; display_filename alone is NFC-normalized.",
            "actual_format is determined from the file signature; raster metadata is read with Pillow.",
            "An .ai file with a PDF signature remains an AI original and is not counted as an extension mismatch.",
            "Dimensions and mode are image metadata, not confirmation that the full image or document renders successfully.",
            "Equal SHA-256 values identify byte-identical duplicates only; visually similar images and PDF/JPEG versions may differ.",
        ],
        "summary": {
            "file_count": len(records),
            "bytes_total": sum(item["bytes"] for item in records),
            "extension_counts": dict(sorted(Counter(item["extension"] for item in records).items())),
            "signature_format_counts": dict(sorted(Counter(item["actual_format"] for item in records).items())),
            "raster_file_count": sum(item["actual_format"] in RASTER_FORMATS for item in records),
            "raster_metadata_error_count": sum("raster_error" in item for item in records),
            "pdf_compatible_ai_count": sum(item["format_description"] == "AI original with PDF signature" for item in records),
            "extension_mismatch_count": len(mismatches),
            "extension_mismatch_paths": mismatches,
            "unknown_format_count": sum(item["actual_format"] == "unknown" for item in records),
            "non_nfc_filename_count": sum(not item["filename_is_nfc"] for item in records),
            "unique_sha256_count": len(by_hash),
            "duplicate_hash_group_count": len(duplicates),
            "duplicate_file_count": sum(item["count"] for item in duplicates),
            "redundant_copy_count": sum(item["count"] - 1 for item in duplicates),
        },
        "duplicate_hash_groups": duplicates,
        "files": records,
        "skipped_entries": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", type=Path, default=PROJECT_ROOT, help="Project containing data/img")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts" / "project_inventory.json", help="Destination JSON file")
    args = parser.parse_args()
    try:
        payload = inventory(args.project)
        output = args.output.expanduser().resolve()
        source = (args.project.expanduser().resolve() / "data" / "img").resolve()
        if output.is_relative_to(source):
            raise ValueError("Output must be outside data/img so source files cannot be overwritten")
        output_directory(output.parent, project=args.project)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps({"output": str(output), "summary": payload["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
