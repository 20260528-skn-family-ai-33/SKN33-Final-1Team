"""Rebuild Gimcheon labeling/OCR diagnostics without modifying data/img.

Run from the repository root: python -m scripts.run_sample
Use --labels-only to inspect annotations without downloading/loading OCR models.
Frontend build and application launch are independent commands.
"""
import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

from scripts.paths import DEFAULT_ARTIFACTS, PROJECT_ROOT, output_directory
from scripts.prepare_labels import EXPECTED_SHA256


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, help="Defaults to the checked-in Gimcheon poster")
    ap.add_argument("--project", type=Path, default=PROJECT_ROOT)
    ap.add_argument("--output", type=Path, default=DEFAULT_ARTIFACTS)
    ap.add_argument("--labels-only", action="store_true", help="Generate/validate reference draft without OCR")
    ap.add_argument("--variants", nargs="+", default=["original", "rgb_2x", "gray_2x", "clahe_2x", "threshold_2x"],
                    choices=["original", "rgb_2x", "gray_2x", "clahe_2x", "threshold_2x"])
    args = ap.parse_args()
    if args.input is None:
        from backend.app.sample import sample_image_path
        source = sample_image_path(args.project.expanduser().resolve() / "data" / "img")
        if source is None:
            ap.error("The Gimcheon poster was not found in the project's data/img directory")
    else:
        source = args.input.expanduser().resolve()
    project = args.project.expanduser().resolve()
    out = output_directory(args.output, source, project)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if original_hash != EXPECTED_SHA256:
        ap.error("Reference labels only apply to the checked-in Gimcheon poster. Use scripts.preprocess for another image.")
    steps = [
        ["scripts.inventory", "--project", str(project), "--output", str(out / "project_inventory.json")],
        ["scripts.prepare_labels", "--input", str(source), "--project", str(project), "--output", str(out)],
    ]
    if not args.labels_only:
        steps.extend([
            ["scripts.preprocess", "--input", str(source), "--project", str(project), "--output", str(out), "--variants", *args.variants],
            ["scripts.compare_ocr", "--project", str(project), "--artifacts", str(out)],
        ])
    steps.append(["scripts.validate_sample", "--input", str(source), "--project", str(project), "--artifacts", str(out)])
    try:
        for module, *arguments in steps:
            print("Running:", module, flush=True)
            subprocess.run([sys.executable, "-m", module, *arguments], cwd=PROJECT_ROOT, check=True)
    finally:
        if hashlib.sha256(source.read_bytes()).hexdigest() != original_hash:
            raise RuntimeError("Source image changed during sample generation")
    print("Generated artifacts:", out)
    print("Reference labels remain an unapproved draft, not training truth or benchmark ground truth.")


if __name__ == "__main__":
    main()
