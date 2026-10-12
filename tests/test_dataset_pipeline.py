"""Portable dataset generation and source-preservation checks, without model downloads."""
import errno
import hashlib
import json
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest
from PIL import Image

from scripts.paths import PROJECT_ROOT, artifact_path, output_directory, resolve_source, source_reference


def test_paths_reject_original_directory_and_source_container(tmp_path):
    image_dir = tmp_path / "data" / "img"
    image_dir.mkdir(parents=True)
    source = image_dir / "source.png"
    source.write_bytes(b"original")
    for destination in (image_dir, image_dir / "generated", tmp_path):
        with pytest.raises(ValueError):
            output_directory(destination, source, tmp_path)
    assert source.read_bytes() == b"original"


def test_artifact_symlink_cannot_overwrite_source(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"original")
    output = tmp_path / "artifacts"
    output.mkdir()
    try:
        (output / "reference.png").symlink_to(source)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314 or exc.errno == errno.ENOSYS:
            pytest.skip("Symbolic links are unavailable or require additional Windows privileges")
        raise
    with pytest.raises(ValueError, match="escapes"):
        artifact_path(output, "reference.png")
    assert source.read_bytes() == b"original"


def test_artifact_parent_path_cannot_overwrite_source(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"original")
    output = tmp_path / "artifacts"
    output.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        artifact_path(output, "../source.png")
    assert source.read_bytes() == b"original"


def test_portable_source_reference_handles_unicode_and_external_input(tmp_path):
    image = tmp_path / unicodedata.normalize("NFD", "김천.png")
    image.write_bytes(b"image")
    reference = source_reference(image, tmp_path)
    reference["path"] = unicodedata.normalize("NFC", reference["path"])
    assert resolve_source(reference, tmp_path).samefile(image)
    external = source_reference(image, tmp_path / "other_project")
    assert not Path(external["path"]).is_absolute()
    with pytest.raises(ValueError, match="--input"):
        resolve_source(external, tmp_path / "other_project")
    assert resolve_source(external, explicit_input=image).samefile(image)
    with pytest.raises(ValueError, match="inside"):
        resolve_source({"path": "../escape", "path_base": "project_root"}, tmp_path)


def test_labels_generate_and_validate_against_checked_in_draft(tmp_path):
    fixture = PROJECT_ROOT / "data" / "labels" / "gimcheon_gimbap_2026"
    expected = json.loads((fixture / "annotations.json").read_text(encoding="utf-8"))
    source = resolve_source(expected["source"])
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    artifacts = tmp_path / "artifacts"
    for module, arguments in (
        ("scripts.prepare_labels", ["--input", str(source), "--output", str(artifacts)]),
        ("scripts.validate_sample", ["--artifacts", str(artifacts), "--project", str(PROJECT_ROOT)]),
    ):
        result = subprocess.run([sys.executable, "-m", module, *arguments], cwd=PROJECT_ROOT,
                                text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
    generated = json.loads((artifacts / "annotations.json").read_text(encoding="utf-8"))
    # Preserve original filesystem spelling across NFC/NFD operating systems.
    for key in ("path", "filename_original"):
        generated["source"][key] = unicodedata.normalize("NFC", generated["source"][key])
        expected["source"][key] = unicodedata.normalize("NFC", expected["source"][key])
    assert generated == expected
    assert json.loads((artifacts / "event_reference.json").read_text(encoding="utf-8")) == json.loads((fixture / "event_reference.json").read_text(encoding="utf-8"))
    report = json.loads((artifacts / "validation.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed" and report["human_verified"] is False
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert str(PROJECT_ROOT) not in (artifacts / "annotations.json").read_text(encoding="utf-8")


def test_preprocessing_restores_coordinates_and_preserves_source(tmp_path, monkeypatch):
    from scripts import preprocess
    source = tmp_path / "external_poster.png"
    Image.new("RGB", (20, 30), "white").save(source)
    before = source.read_bytes()
    output = tmp_path / "generated"

    def recognize(path):
        with Image.open(path) as image:
            size = list(image.size)
        return {"engine": "easyocr", "status": "success", "execution_target": "local", "device": "cpu",
                "input_dimensions": size, "records": [{"id": "ocr0", "text": "예시", "confidence": 0.9,
                "bbox_xyxy": [4, 6, 12, 18], "polygon": [[4, 6], [12, 6], [12, 18], [4, 18]]}]}

    monkeypatch.setattr(preprocess, "recognize_image", recognize)
    monkeypatch.setattr(sys, "argv", ["preprocess", "--input", str(source), "--output", str(output), "--variants", "rgb_2x"])
    preprocess.main()
    result = json.loads((output / "ocr" / "rgb_2x.json").read_text(encoding="utf-8"))
    assert result["records"][0]["bbox_xyxy"] == [2, 3, 6, 9]
    assert result["records"][0]["polygon"] == [[2, 3], [6, 3], [6, 9], [2, 9]]
    manifest = json.loads((output / "preprocessing_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"]["path"] == source.name
    assert manifest["source"]["path_base"] == "external_input"
    assert manifest["source_unchanged"] and source.read_bytes() == before
