"""Read existing stored media by authorized metadata, then verify actual bytes.

This module owns no uploads, storage roots, database connections or credentials.
Catalogs must authorize a trusted caller and bind a file to its immutable version.
Model adapters receive verified bytes, never an arbitrary request-supplied path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
from typing import Literal, Protocol
import warnings

from pydantic import BaseModel, Field

from .contracts import AssetRef, Context, Contract, DocumentRef, Hash, Identifier
from .ports import AdapterFailure


class StoredFileMetadata(Contract):
    """Internal catalog result; storage_key is not part of the role request API."""
    file_id: Identifier
    project_id: Identifier
    version_id: Identifier
    kind: Literal["pdf", "text", "poster"]
    storage_key: str = Field(min_length=1, repr=False)
    media_type: Literal["application/pdf", "text/plain", "image/png", "image/jpeg", "image/webp"]
    byte_size: int = Field(gt=0, strict=True)
    sha256: Hash
    width: int | None = Field(default=None, gt=0, strict=True)
    height: int | None = Field(default=None, gt=0, strict=True)
    coordinate_space: Literal["encoded", "display"] = "display"


class FileCatalog(Protocol):
    def lookup_document(self, context: Context, reference: DocumentRef) -> StoredFileMetadata: ...
    def lookup_asset(self, context: Context, reference: AssetRef) -> StoredFileMetadata: ...


class StoredBytes(Protocol):
    def read(self, metadata: StoredFileMetadata, maximum: int) -> bytes:
        """Read at most maximum + 1 bytes using the existing storage backend."""
        ...


@dataclass(frozen=True)
class FileLimits:
    max_document_bytes: int = 20 * 1024 * 1024
    max_document_pages: int = 50
    max_asset_bytes: int = 40 * 1024 * 1024
    max_image_pixels: int = 10_000_000
    max_image_dimension: int = 8192

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError("File limits must be positive integers")


@dataclass(frozen=True)
class VerifiedDocument:
    context: Context
    reference: DocumentRef
    content: bytes = field(repr=False)
    page_count: int

    @property
    def byte_size(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class VerifiedPoster:
    context: Context
    reference: AssetRef
    content: bytes = field(repr=False)
    width: int
    height: int
    encoded_width: int
    encoded_height: int
    exif_orientation: int
    coordinate_space: Literal["encoded", "display"]

    @property
    def byte_size(self) -> int:
        return len(self.content)


def _fail(code: str, message: str):
    raise AdapterFailure(code, message)


def _checked(model, value, *, metadata=False):
    try:
        if isinstance(value, BaseModel):
            if not isinstance(value, model):
                raise ValueError("unexpected contract")
            value = value.model_dump(mode="python", warnings=False)
        return model.model_validate(value).model_copy(deep=True)
    except (ValueError, TypeError):
        _fail("FILE_METADATA_INVALID" if metadata else "CONTRACT_INVALID",
              "The file metadata or reference does not match its contract.")


def inspect_document(content: bytes, limits: FileLimits | None = None) -> int:
    """Shared upload/read PDF container check; no OCR, model, rendering or writes."""
    limits = limits or FileLimits()
    if not isinstance(content, bytes) or not content.startswith(b"%PDF-"):
        _fail("FILE_FORMAT_INVALID", "The document is not a readable PDF.")
    if len(content) > limits.max_document_bytes:
        _fail("FILE_TOO_LARGE", "The PDF exceeds the configured byte limit.")
    import pymupdf
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.is_encrypted or document.needs_pass or document.page_count < 1:
                _fail("FILE_FORMAT_INVALID", "An unencrypted PDF with readable pages is required.")
            if document.page_count > limits.max_document_pages:
                _fail("FILE_TOO_LARGE", "The PDF exceeds the configured page limit.")
            count = document.page_count
            for page in document:
                if page.rect.is_empty or page.rect.is_infinite:
                    _fail("FILE_FORMAT_INVALID", "The PDF contains an invalid page.")
    except AdapterFailure:
        raise
    except Exception:
        _fail("FILE_FORMAT_INVALID", "The PDF container could not be read.")
    return count


class VerifiedFileAccess:
    """Resolve one authorized file/version and verify the bytes actually returned.

Construction performs no I/O. A catalog must bind an authenticated actor outside
Context; the actor_id sent in a role request is not proof of authorization.
"""
    def __init__(self, catalog: FileCatalog, reader: StoredBytes, *, limits: FileLimits | None = None):
        self.catalog, self.reader = catalog, reader
        self.limits = limits or FileLimits()

    def _metadata(self, context, reference, kind):
        try:
            lookup = self.catalog.lookup_document if kind in ("pdf","text") else self.catalog.lookup_asset
            metadata = _checked(StoredFileMetadata, lookup(context.model_copy(deep=True),
                                                          reference.model_copy(deep=True)), metadata=True)
        except AdapterFailure:
            raise
        except Exception:
            _fail("FILE_UNAVAILABLE", "Authorized file metadata could not be resolved.")
        if metadata.project_id != context.project_id:
            _fail("FILE_SCOPE_MISMATCH", "The file belongs to a different project.")
        if metadata.file_id != reference.file_id or metadata.kind != kind:
            _fail("FILE_REFERENCE_MISMATCH", "The file does not match the requested media reference.")
        version = reference.document_version_id if kind in ("pdf","text") else reference.asset_version_id
        if metadata.version_id != version:
            _fail("FILE_VERSION_MISMATCH", "The file is not bound to the requested version.")
        if metadata.media_type != reference.media_type:
            _fail("FILE_MEDIA_TYPE_MISMATCH", "The stored media type differs from the reference.")
        if metadata.sha256 != reference.sha256:
            _fail("FILE_HASH_MISMATCH", "The stored hash differs from the reference.")
        if kind == "poster":
            if metadata.width is None or metadata.height is None:
                _fail("FILE_METADATA_INVALID", "Poster metadata must specify its coordinate dimensions.")
            if (metadata.width, metadata.height) != (reference.width, reference.height):
                _fail("IMAGE_DIMENSIONS_MISMATCH", "Poster dimensions differ from the reference.")
        return metadata

    def _bytes(self, metadata, maximum):
        if metadata.byte_size > maximum:
            _fail("FILE_TOO_LARGE", "The stored file exceeds the configured byte limit.")
        try:
            content = self.reader.read(metadata.model_copy(deep=True), maximum)
        except AdapterFailure:
            raise
        except Exception:
            _fail("FILE_UNAVAILABLE", "The stored file could not be read.")
        if not isinstance(content, bytes):
            _fail("FILE_FORMAT_INVALID", "The storage reader must return immutable bytes.")
        if len(content) > maximum:
            _fail("FILE_TOO_LARGE", "The file exceeds the configured byte limit.")
        if len(content) != metadata.byte_size:
            _fail("FILE_SIZE_MISMATCH", "The file length differs from stored metadata.")
        if sha256(content).hexdigest() != metadata.sha256:
            _fail("FILE_HASH_MISMATCH", "The actual file bytes differ from the stored hash.")
        return content

    def read_document(self, context: Context, reference: DocumentRef) -> VerifiedDocument:
        context, reference = _checked(Context, context), _checked(DocumentRef, reference)
        if context.document_version_id != reference.document_version_id:
            _fail("DOCUMENT_VERSION_MISMATCH", "The document reference differs from the execution context.")
        metadata = self._metadata(context, reference, "text" if reference.media_type=='text/plain' else "pdf")
        content = self._bytes(metadata, self.limits.max_document_bytes)
        from .requirement_text import inspect_requirement
        count = inspect_requirement(content,reference.media_type,self.limits)
        if count != reference.page_count:
            _fail("PDF_PAGE_COUNT_MISMATCH", "The PDF page count differs from the reference.")
        return VerifiedDocument(context, reference, content, count)

    def read_asset(self, context: Context, reference: AssetRef) -> VerifiedPoster:
        context, reference = _checked(Context, context), _checked(AssetRef, reference)
        if context.asset_version_id != reference.asset_version_id:
            _fail("ASSET_VERSION_MISMATCH", "The asset reference differs from the execution context.")
        metadata = self._metadata(context, reference, "poster")
        content = self._bytes(metadata, self.limits.max_asset_bytes)
        from PIL import Image

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as image:
                    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format)
                    if mime is None or getattr(image, "n_frames", 1) != 1:
                        _fail("FILE_FORMAT_INVALID", "A static PNG, JPEG or WebP poster is required.")
                    if mime != reference.media_type:
                        _fail("FILE_MEDIA_TYPE_MISMATCH", "The actual image format differs from the reference.")
                    encoded_width, encoded_height = image.size
                    if (min(image.size) < 1 or max(image.size) > self.limits.max_image_dimension
                            or encoded_width * encoded_height > self.limits.max_image_pixels):
                        _fail("FILE_TOO_LARGE", "The poster exceeds the configured pixel limits.")
                    image.verify()
                with Image.open(BytesIO(content)) as image:
                    image.load()
                    orientation = image.getexif().get(274, 1)
                    if type(orientation) is not int or orientation not in range(1, 9):
                        _fail("FILE_FORMAT_INVALID", "The poster contains an invalid orientation.")
        except AdapterFailure:
            raise
        except Exception:
            _fail("FILE_FORMAT_INVALID", "The poster image could not be decoded.")
        width, height = encoded_width, encoded_height
        if metadata.coordinate_space == "display" and orientation in {5, 6, 7, 8}:
            width, height = height, width
        if (width, height) != (reference.width, reference.height):
            _fail("IMAGE_DIMENSIONS_MISMATCH", "The actual poster dimensions differ from the reference.")
        return VerifiedPoster(context, reference, content, width, height, encoded_width,
                              encoded_height, orientation, metadata.coordinate_space)
