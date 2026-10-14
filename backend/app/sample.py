"""Locate the repository's sample poster without relying on macOS normalization."""
from pathlib import Path
import unicodedata

from PIL import Image, UnidentifiedImageError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_FILENAME = "2026_김천_김밥축제.jpg"
SAMPLE_FORMATS = {"PNG", "JPEG", "WEBP"}


def sample_image_path(image_directory: Path | None = None) -> Path | None:
    """Return a validated raster, matching NFC and NFD filenames equally.

    The checked-in .jpg file currently contains PNG pixels. The caller must use
    the decoded image format for its HTTP media type instead of the suffix.
    """
    directory = image_directory if image_directory is not None else PROJECT_ROOT / "data" / "img"
    if not directory.is_dir():
        return None
    for candidate in directory.iterdir():
        if unicodedata.normalize("NFC", candidate.name) != SAMPLE_FILENAME or not candidate.is_file():
            continue
        try:
            with Image.open(candidate) as image:
                if image.format not in SAMPLE_FORMATS or getattr(image, "n_frames", 1) != 1:
                    continue
                image.verify()
            return candidate
        except (UnidentifiedImageError, OSError, ValueError):
            continue
    return None
