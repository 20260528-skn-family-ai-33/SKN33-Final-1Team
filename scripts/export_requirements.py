#!/usr/bin/env python3
"""Export the shared runtime requirements from the existing uv lockfile."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "requirements.txt"
HEADER = (
    "# Generated from pyproject.toml and uv.lock; do not edit directly.\n"
    "# Update: python scripts/export_requirements.py\n"
    "# Common app only; GPU worker dependencies require separate environments.\n"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check for export drift")
    args = parser.parse_args()
    try:
        exported = subprocess.run(
            ["uv", "export", "--frozen", "--offline", "--format", "requirements-txt",
             "--no-dev", "--no-emit-project", "--no-hashes", "--no-header", "--no-annotate"],
            cwd=ROOT, text=True, encoding="utf-8", capture_output=True, check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"Could not export locked dependencies: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        return 1
    content = HEADER + exported.replace("\r\n", "\n")
    if args.check:
        if not DESTINATION.exists() or DESTINATION.read_text(encoding="utf-8") != content:
            print("requirements.txt differs from uv.lock; run python scripts/export_requirements.py", file=sys.stderr)
            return 1
        print("REQUIREMENTS_UP_TO_DATE")
    else:
        DESTINATION.write_text(content, encoding="utf-8", newline="\n")
        print("REQUIREMENTS_EXPORTED: requirements.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
