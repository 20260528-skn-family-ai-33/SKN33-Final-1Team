"""Actual byte decoding at the framework file boundary, without DB or network."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO

from PIL import Image
import pymupdf
import pytest

from backend.agent_framework.contracts import AssetRef, Context, DocumentRef, ExtractRequest
from backend.agent_framework.adapters.file_backed import FileBackedExtractor, FileBackedInspector
from backend.agent_framework.adapters.generate_examples import role_examples
from backend.agent_framework.adapters.synthetic import make_agents
from backend.agent_framework.invocation import invoke_role
from backend.agent_framework.files import FileLimits, StoredFileMetadata, VerifiedFileAccess
from backend.agent_framework.ports import AdapterFailure


def context():
    return Context(project_id="project-1", run_id="run-1", actor_id="reviewer-1",
                   document_version_id="document-v1", criteria_version_id="criteria-v1",
                   asset_version_id="poster-v1")


def pdf_bytes(pages=1, *, encrypted=False):
    with pymupdf.open() as document:
        for _ in range(pages):
            document.new_page(width=240, height=320)
        options = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "owner_pw": "owner-test", "user_pw": "user-test"} if encrypted else {}
        return document.tobytes(**options)


def image_bytes(format="PNG", size=(40, 24), *, orientation=None, animated=False):
    image = Image.new("RGB", size, "red")
    options = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        options["exif"] = exif
    if animated:
        options.update(save_all=True, append_images=[Image.new("RGB", size, "blue")], duration=80, loop=0)
    stream = BytesIO()
    image.save(stream, format=format, **options)
    return stream.getvalue()


class Catalog:
    def __init__(self, metadata):
        self.metadata = metadata
        self.calls = []

    def lookup_document(self, ctx, reference):
        self.calls.append(("pdf", ctx, reference))
        return self.metadata

    def lookup_asset(self, ctx, reference):
        self.calls.append(("poster", ctx, reference))
        return self.metadata


class Reader:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def read(self, metadata, maximum):
        self.calls.append((metadata, maximum))
        return self.payload[:maximum + 1]


def pdf_setup(payload=None, *, pages=1, limits=None, metadata_updates=None):
    payload = pdf_bytes(pages) if payload is None else payload
    ctx = context()
    reference = DocumentRef(file_id="file-pdf-1", document_version_id=ctx.document_version_id,
                            sha256=sha256(payload).hexdigest(), page_count=pages)
    metadata = StoredFileMetadata(file_id=reference.file_id, project_id=ctx.project_id,
        version_id=reference.document_version_id, kind="pdf", storage_key="project-1/source.pdf",
        media_type=reference.media_type, byte_size=len(payload), sha256=reference.sha256)
    if metadata_updates:
        metadata = metadata.model_copy(update=metadata_updates)
    catalog, reader = Catalog(metadata), Reader(payload)
    access = VerifiedFileAccess(catalog, reader, limits=limits or FileLimits())
    return access, catalog, reader, ctx, reference


def poster_setup(payload=None, *, format="PNG", size=(40, 24), coordinate_space="display", limits=None,
                 metadata_dimensions=True, metadata_updates=None):
    payload = image_bytes(format, size) if payload is None else payload
    media_type = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[format]
    ctx = context()
    reference = AssetRef(file_id="file-poster-1", asset_version_id=ctx.asset_version_id,
                         sha256=sha256(payload).hexdigest(), media_type=media_type,
                         width=size[0], height=size[1])
    metadata = StoredFileMetadata(file_id=reference.file_id, project_id=ctx.project_id,
        version_id=reference.asset_version_id, kind="poster", storage_key="project-1/poster.img",
        media_type=media_type, byte_size=len(payload), sha256=reference.sha256,
        width=size[0] if metadata_dimensions else None,
        height=size[1] if metadata_dimensions else None, coordinate_space=coordinate_space)
    if metadata_updates:
        metadata = metadata.model_copy(update=metadata_updates)
    catalog, reader = Catalog(metadata), Reader(payload)
    access = VerifiedFileAccess(catalog, reader, limits=limits or FileLimits())
    return access, catalog, reader, ctx, reference


def code_of(action):
    with pytest.raises(AdapterFailure) as failure:
        action()
    return failure.value.code


def test_pdf_actual_bytes_page_count_and_reference_are_verified_without_mutating_inputs():
    access, catalog, reader, ctx, reference = pdf_setup(pages=2)
    originals = (ctx.model_dump(), reference.model_dump(), catalog.metadata.model_dump())
    verified = access.read_document(ctx, reference)
    assert verified.content == reader.payload
    assert verified.page_count == 2 and verified.byte_size == len(reader.payload)
    assert verified.context == ctx and verified.reference == reference
    assert originals == (ctx.model_dump(), reference.model_dump(), catalog.metadata.model_dump())
    assert [call[0] for call in catalog.calls] == ["pdf"] and len(reader.calls) == 1
    assert reader.calls[0][1] <= FileLimits().max_document_bytes
    assert repr(reader.payload) not in repr(verified)


@pytest.mark.parametrize("format", ("PNG", "JPEG", "WEBP"))
def test_supported_images_are_decoded_and_original_bytes_preserved(format):
    access, catalog, reader, ctx, reference = poster_setup(format=format)
    originals = (ctx.model_dump(), reference.model_dump(), catalog.metadata.model_dump())
    verified = access.read_asset(ctx, reference)
    assert verified.content == reader.payload
    assert (verified.width, verified.height) == (40, 24)
    assert (verified.encoded_width, verified.encoded_height) == (40, 24)
    assert verified.coordinate_space == "display"
    assert verified.context == ctx and verified.reference == reference
    assert originals == (ctx.model_dump(), reference.model_dump(), catalog.metadata.model_dump())
    assert len(catalog.calls) == len(reader.calls) == 1
    assert repr(reader.payload) not in repr(verified)


@pytest.mark.parametrize("kind", ("pdf", "poster"))
def test_context_reference_version_mismatch_is_rejected_before_catalog_or_reader(kind):
    setup = pdf_setup if kind == "pdf" else poster_setup
    access, catalog, reader, ctx, reference = setup()
    field = "document_version_id" if kind == "pdf" else "asset_version_id"
    wrong = ctx.model_copy(update={field: "different-version"})
    method = access.read_document if kind == "pdf" else access.read_asset
    assert code_of(lambda: method(wrong, reference)) == ("DOCUMENT_VERSION_MISMATCH" if kind == "pdf" else "ASSET_VERSION_MISMATCH")
    assert catalog.calls == reader.calls == []


@pytest.mark.parametrize("kind", ("pdf", "poster"))
@pytest.mark.parametrize("field,value,code", (
    ("project_id", "other-project", "FILE_SCOPE_MISMATCH"),
    ("file_id", "other-file", "FILE_REFERENCE_MISMATCH"),
    ("version_id", "other-version", "FILE_VERSION_MISMATCH"),
    ("sha256", "0" * 64, "FILE_HASH_MISMATCH"),
))
def test_catalog_scope_reference_and_digest_are_checked_before_bytes(kind, field, value, code):
    setup = pdf_setup if kind == "pdf" else poster_setup
    access, catalog, reader, ctx, reference = setup(metadata_updates={field: value})
    method = access.read_document if kind == "pdf" else access.read_asset
    assert code_of(lambda: method(ctx, reference)) == code
    assert len(catalog.calls) == 1 and not reader.calls


@pytest.mark.parametrize("kind", ("pdf", "poster"))
def test_media_type_mismatch_rejected_before_reader(kind):
    setup = pdf_setup if kind == "pdf" else poster_setup
    access, _catalog, reader, ctx, reference = setup(metadata_updates={"media_type": "image/jpeg"})
    method = access.read_document if kind == "pdf" else access.read_asset
    assert code_of(lambda: method(ctx, reference)) == "FILE_MEDIA_TYPE_MISMATCH"
    assert not reader.calls


@pytest.mark.parametrize("kind", ("pdf", "poster"))
def test_malformed_catalog_metadata_is_revalidated_before_reader(kind):
    setup = pdf_setup if kind == "pdf" else poster_setup
    access, _catalog, reader, ctx, reference = setup(metadata_updates={"byte_size": -1})
    method = access.read_document if kind == "pdf" else access.read_asset
    assert code_of(lambda: method(ctx, reference)) == "FILE_METADATA_INVALID"
    assert not reader.calls


@pytest.mark.parametrize("kind", ("pdf", "poster"))
def test_bypassed_request_validation_never_reaches_catalog(kind):
    setup = pdf_setup if kind == "pdf" else poster_setup
    access, catalog, reader, ctx, reference = setup()
    field = "page_count" if kind == "pdf" else "width"
    malformed = reference.model_copy(update={field: 0})
    method = access.read_document if kind == "pdf" else access.read_asset
    assert code_of(lambda: method(ctx, malformed)) == "CONTRACT_INVALID"
    assert catalog.calls == reader.calls == []


@pytest.mark.parametrize("kind", ("pdf", "poster"))
def test_declared_oversize_is_rejected_without_reading(kind):
    setup = pdf_setup if kind == "pdf" else poster_setup
    limits = FileLimits(max_document_bytes=10, max_asset_bytes=10)
    access, _catalog, reader, ctx, reference = setup(limits=limits)
    method = access.read_document if kind == "pdf" else access.read_asset
    assert code_of(lambda: method(ctx, reference)) == "FILE_TOO_LARGE"
    assert not reader.calls


@pytest.mark.parametrize("kind", ("pdf", "poster"))
def test_same_length_modified_bytes_cannot_pass_catalog_hash(kind):
    setup = pdf_setup if kind == "pdf" else poster_setup
    access, _catalog, reader, ctx, reference = setup()
    reader.payload = reader.payload[:-1] + bytes([reader.payload[-1] ^ 1])
    method = access.read_document if kind == "pdf" else access.read_asset
    assert code_of(lambda: method(ctx, reference)) == "FILE_HASH_MISMATCH"
    assert len(reader.calls) == 1


@pytest.mark.parametrize("kind", ("pdf", "poster"))
def test_actual_byte_count_must_match_catalog(kind):
    setup = pdf_setup if kind == "pdf" else poster_setup
    access, _catalog, reader, ctx, reference = setup()
    reader.payload = reader.payload[:-1]
    method = access.read_document if kind == "pdf" else access.read_asset
    assert code_of(lambda: method(ctx, reference)) == "FILE_SIZE_MISMATCH"


def test_reader_cannot_exceed_document_limit_by_lying_about_declared_size():
    payload = pdf_bytes()
    access, _catalog, reader, ctx, reference = pdf_setup(payload,
        limits=FileLimits(max_document_bytes=len(payload)), metadata_updates={"byte_size": len(payload) - 1})
    reader.payload = payload + b"extra"
    assert code_of(lambda: access.read_document(ctx, reference)) == "FILE_TOO_LARGE"
    assert len(reader.calls) == 1 and reader.calls[0][1] <= len(payload)


@pytest.mark.parametrize("kind", ("pdf", "poster"))
def test_missing_reader_file_is_safe_unavailable_error(kind):
    setup = pdf_setup if kind == "pdf" else poster_setup
    access, _catalog, reader, ctx, reference = setup()

    def missing(_metadata, _maximum):
        raise FileNotFoundError("/private/local/token-never-print")

    reader.read = missing
    method = access.read_document if kind == "pdf" else access.read_asset
    with pytest.raises(AdapterFailure) as failure:
        method(ctx, reference)
    assert failure.value.code == "FILE_UNAVAILABLE"
    assert "token-never-print" not in str(failure.value)


@pytest.mark.parametrize("payload", (b"plain text pretending to be a PDF", b"%PDF-1.7\nbroken-object-without-trailer"))
def test_pdf_type_and_decodability_are_checked_after_valid_hash(payload):
    access, _catalog, _reader, ctx, reference = pdf_setup(payload)
    assert code_of(lambda: access.read_document(ctx, reference)) == "FILE_FORMAT_INVALID"


def test_password_protected_pdf_is_rejected():
    access, _catalog, _reader, ctx, reference = pdf_setup(pdf_bytes(encrypted=True))
    assert code_of(lambda: access.read_document(ctx, reference)) == "FILE_FORMAT_INVALID"


def test_actual_pdf_page_count_must_match_reference():
    access, _catalog, _reader, ctx, reference = pdf_setup(pdf_bytes(pages=2), pages=1)
    assert code_of(lambda: access.read_document(ctx, reference)) == "PDF_PAGE_COUNT_MISMATCH"


def test_actual_pdf_page_limit_cannot_be_bypassed_with_small_reference_count():
    access, _catalog, _reader, ctx, reference = pdf_setup(pdf_bytes(pages=2), pages=1,
                                                        limits=FileLimits(max_document_pages=1))
    assert code_of(lambda: access.read_document(ctx, reference)) == "FILE_TOO_LARGE"


def test_image_mime_spoof_is_rejected_even_with_matching_hash_and_size():
    access, _catalog, _reader, ctx, reference = poster_setup(image_bytes("JPEG"), format="PNG")
    assert code_of(lambda: access.read_asset(ctx, reference)) == "FILE_MEDIA_TYPE_MISMATCH"


@pytest.mark.parametrize("payload", (b"not a real image", b"\x89PNG\r\n\x1a\ninvalid-png-chunks"))
def test_corrupt_image_is_rejected_after_valid_hash(payload):
    access, _catalog, _reader, ctx, reference = poster_setup(payload)
    assert code_of(lambda: access.read_asset(ctx, reference)) == "FILE_FORMAT_INVALID"


def test_animated_image_is_not_accepted_as_single_poster():
    payload = image_bytes("PNG", animated=True)
    access, _catalog, _reader, ctx, reference = poster_setup(payload)
    assert code_of(lambda: access.read_asset(ctx, reference)) == "FILE_FORMAT_INVALID"


def test_actual_image_dimensions_cannot_be_spoofed_by_reference_and_catalog():
    access, _catalog, _reader, ctx, reference = poster_setup(image_bytes(size=(40, 24)), size=(24, 40))
    assert code_of(lambda: access.read_asset(ctx, reference)) == "IMAGE_DIMENSIONS_MISMATCH"


def test_catalog_dimensions_are_checked_before_reading_bytes():
    access, _catalog, reader, ctx, reference = poster_setup(metadata_updates={"width": 39})
    assert code_of(lambda: access.read_asset(ctx, reference)) == "IMAGE_DIMENSIONS_MISMATCH"
    assert not reader.calls


def test_poster_requires_catalog_dimensions_before_reading_bytes():
    access, _catalog, reader, ctx, reference = poster_setup(metadata_dimensions=False)
    assert code_of(lambda: access.read_asset(ctx, reference)) == "FILE_METADATA_INVALID"
    assert not reader.calls


@pytest.mark.parametrize("limits", (FileLimits(max_image_pixels=900), FileLimits(max_image_dimension=32)))
def test_actual_image_bounds_cannot_be_hidden_by_small_declared_dimensions(limits):
    # Both declared sizes fit the limit; only the encoded pixels exceed it.
    access, _catalog, _reader, ctx, reference = poster_setup(
        image_bytes(size=(40, 24)), size=(20, 20), limits=limits)
    assert code_of(lambda: access.read_asset(ctx, reference)) == "FILE_TOO_LARGE"


@pytest.mark.parametrize("orientation,space,size", (
    (1, "display", (40, 24)), (3, "display", (40, 24)),
    (6, "display", (24, 40)), (8, "display", (24, 40)),
    (6, "encoded", (40, 24)), (8, "encoded", (40, 24)),
))
def test_exif_coordinate_space_is_explicit_and_does_not_reencode_image(orientation, space, size):
    payload = image_bytes("JPEG", orientation=orientation)
    access, _catalog, reader, ctx, reference = poster_setup(payload, format="JPEG", size=size, coordinate_space=space)
    verified = access.read_asset(ctx, reference)
    assert (verified.width, verified.height) == size
    assert (verified.encoded_width, verified.encoded_height) == (40, 24)
    assert verified.exif_orientation == orientation
    assert verified.coordinate_space == space
    assert verified.content == reader.payload == payload
    assert sha256(verified.content).hexdigest() == reference.sha256


def test_exif_display_dimensions_do_not_accept_encoded_reference_sizes():
    payload = image_bytes("JPEG", orientation=6)
    access, _catalog, _reader, ctx, reference = poster_setup(payload, format="JPEG", size=(40, 24), coordinate_space="display")
    assert code_of(lambda: access.read_asset(ctx, reference)) == "IMAGE_DIMENSIONS_MISMATCH"


@pytest.mark.parametrize("kind", ("pdf", "poster"))
def test_catalog_cannot_mutate_expected_scope_to_authorize_another_project(kind):
    setup = pdf_setup if kind == "pdf" else poster_setup
    access, catalog, reader, ctx, reference = setup()
    expected = ctx.model_dump()

    def forged_context(value, _reference):
        object.__setattr__(value, "project_id", "other-project")
        return catalog.metadata.model_copy(update={"project_id": "other-project"})

    if kind == "pdf":
        catalog.lookup_document = forged_context
    else:
        catalog.lookup_asset = forged_context
    method = access.read_document if kind == "pdf" else access.read_asset
    assert code_of(lambda: method(ctx, reference)) == "FILE_SCOPE_MISMATCH"
    assert not reader.calls and ctx.model_dump() == expected


@pytest.mark.parametrize("kind", ("pdf", "poster"))
def test_reader_cannot_rewrite_hash_baseline_to_accept_modified_bytes(kind):
    setup = pdf_setup if kind == "pdf" else poster_setup
    access, catalog, reader, ctx, reference = setup()
    modified = reader.payload[:-1] + bytes([reader.payload[-1] ^ 1])
    expected_hash = catalog.metadata.sha256

    def forged_metadata(metadata, _maximum):
        object.__setattr__(metadata, "sha256", sha256(modified).hexdigest())
        return modified

    reader.read = forged_metadata
    method = access.read_document if kind == "pdf" else access.read_asset
    assert code_of(lambda: method(ctx, reference)) == "FILE_HASH_MISMATCH"
    assert catalog.metadata.sha256 == reference.sha256 == expected_hash


def inspection_request(ctx, reference):
    request = role_examples()["A04_A05"][0]
    evidence = request.evidence.model_copy(update={"context": ctx, "evidence": tuple(
        item.model_copy(update={"project_id": ctx.project_id}) for item in request.evidence.evidence)})
    return request.model_copy(update={"context": ctx, "asset": reference, "evidence": evidence})


@pytest.mark.parametrize("role", ("A02", "A04_A05"))
def test_file_backed_callbacks_receive_verified_real_bytes_through_shared_boundary(role):
    setup = pdf_setup if role == "A02" else poster_setup
    access, _catalog, reader, ctx, reference = setup()
    request = ExtractRequest(context=ctx, document=reference) if role == "A02" else inspection_request(ctx, reference)
    calls = []

    def model_callback(value, verified):
        calls.append((value, verified))
        assert verified.content == reader.payload
        assert verified.context == value.context
        assert verified.reference == (value.document if role == "A02" else value.asset)
        return make_agents().extractor.extract(value) if role == "A02" else make_agents().inspector.inspect(value)

    adapter = FileBackedExtractor(access, model_callback) if role == "A02" else FileBackedInspector(access, model_callback)
    result = invoke_role(role, adapter, request)
    assert result.context == ctx and result.synthetic
    assert len(calls) == 1


@pytest.mark.parametrize("role", ("A02", "A04_A05"))
def test_failed_file_verification_prevents_any_model_callback(role):
    setup = pdf_setup if role == "A02" else poster_setup
    access, _catalog, reader, ctx, reference = setup()
    request = ExtractRequest(context=ctx, document=reference) if role == "A02" else inspection_request(ctx, reference)
    calls = []
    reader.payload = reader.payload[:-1] + bytes([reader.payload[-1] ^ 1])
    callback = lambda value, verified: calls.append((value, verified))
    adapter = FileBackedExtractor(access, callback) if role == "A02" else FileBackedInspector(access, callback)
    assert code_of(lambda: invoke_role(role, adapter, request)) == "FILE_HASH_MISMATCH"
    assert calls == []


@pytest.mark.parametrize("kind", ("pdf", "poster"))
def test_catalog_media_kind_must_match_the_requested_reference(kind):
    setup = pdf_setup if kind == "pdf" else poster_setup
    access, _catalog, reader, ctx, reference = setup(metadata_updates={"kind": "poster" if kind == "pdf" else "pdf"})
    method = access.read_document if kind == "pdf" else access.read_asset
    assert code_of(lambda: method(ctx, reference)) == "FILE_REFERENCE_MISMATCH"
    assert not reader.calls


@pytest.mark.parametrize("kind", ("pdf", "poster"))
def test_context_constructed_without_validation_is_rejected_before_io(kind):
    setup = pdf_setup if kind == "pdf" else poster_setup
    access, catalog, reader, ctx, reference = setup()
    malformed = ctx.model_copy(update={"project_id": ""})
    method = access.read_document if kind == "pdf" else access.read_asset
    assert code_of(lambda: method(malformed, reference)) == "CONTRACT_INVALID"
    assert catalog.calls == reader.calls == []
