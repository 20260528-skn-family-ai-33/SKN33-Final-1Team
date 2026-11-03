"""The shared sample comes from repository data, never a developer's machine."""
import hashlib
import unicodedata

from fastapi.testclient import TestClient
from PIL import Image

from backend.app import api
from backend.app.sample import PROJECT_ROOT, SAMPLE_FILENAME, sample_image_path


def test_sample_lookup_accepts_nfc_and_nfd_names(tmp_path):
    for form in ("NFC", "NFD"):
        directory = tmp_path / form
        directory.mkdir()
        poster = directory / unicodedata.normalize(form, SAMPLE_FILENAME)
        Image.new("RGB", (12, 16), "blue").save(poster, format="PNG")
        assert sample_image_path(directory) == poster


def test_sample_lookup_rejects_missing_or_invalid_image(tmp_path):
    assert sample_image_path(tmp_path / "missing") is None
    (tmp_path / SAMPLE_FILENAME).write_bytes(b"not an image")
    assert sample_image_path(tmp_path) is None


def test_repo_sample_is_served_with_actual_format_and_original_bytes(tmp_path):
    sample = sample_image_path()
    assert sample is not None
    assert sample.parent == PROJECT_ROOT / "data" / "img"
    source = sample.read_bytes()
    app = api.create_app(review_root=tmp_path / "reviews", runtime_status=lambda: {"ready": False})
    with TestClient(app) as client:
        result = client.get("/api/sample/image")
    assert result.status_code == 200
    with Image.open(sample) as decoded:
        assert result.headers["content-type"] == Image.MIME[decoded.format]
    assert hashlib.sha256(result.content).digest() == hashlib.sha256(source).digest()


def test_sample_api_returns_not_found_when_source_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "sample_image_path", lambda: None)
    app = api.create_app(review_root=tmp_path / "reviews", runtime_status=lambda: {"ready": False})
    with TestClient(app) as client:
        assert client.get("/api/sample/image").status_code == 404
