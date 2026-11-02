"""Read adapters for existing product catalogs and storage implementations.

Constructors do not initialize stores or open a database. Authentication belongs
to the embedding application: bind a trusted identity, never an actor ID copied
from an unauthenticated request. Lookups recheck current account/project access.
These adapters do not upload files, migrate schemas or combine the two products.
"""
from __future__ import annotations

from uuid import UUID

from ..contracts import AssetRef, Context, DocumentRef
from ..files import StoredFileMetadata
from ..ports import AdapterFailure


def _fail(code):
    messages = {
        "FILE_ACCESS_DENIED": "The bound identity cannot access this file.",
        "FILE_REFERENCE_MISMATCH": "The file is not linked to the requested project and version.",
        "FILE_METADATA_INVALID": "The registered file metadata is inconsistent.",
        "FILE_SOURCE_UNSUPPORTED": "This catalog does not support the requested file source.",
        "FILE_UNAVAILABLE": "The registered file is unavailable.",
        "FILE_STORAGE_UNAVAILABLE": "The existing file service could not complete the read.",
        "FILE_INTEGRITY_MISMATCH": "The stored bytes do not match their registered metadata.",
    }
    raise AdapterFailure(code, messages[code])


def _authorize_error(error):
    status = getattr(error, "status_code", getattr(error, "status", None))
    if status in (401, 403, 404):
        _fail("FILE_ACCESS_DENIED")
    if status == 422:
        _fail("FILE_REFERENCE_MISMATCH")
    _fail("FILE_STORAGE_UNAVAILABLE")


def _positive_integer(value):
    if type(value) is not int or value <= 0:
        _fail("FILE_METADATA_INVALID")
    return value


def _maximum(value):
    if type(value) is not int or value < 0:
        _fail("FILE_METADATA_INVALID")
    return value


def _creative_key(storage_key, project_id):
    from backend.creative_checker.storage import validate_key

    try:
        checked = validate_key(storage_key)
    except (ValueError, TypeError):
        _fail("FILE_METADATA_INVALID")
    if not checked.startswith(str(project_id) + "/"):
        _fail("FILE_REFERENCE_MISMATCH")
    return checked


class CreativeCheckerCatalog:
    """Reuse the separate cc_* product's version rows and access services.

    session_factory must provide a fresh owned SQLAlchemy session. The caller
    binds an already authenticated user ID; this is not a login/session service.
    Reads use no_autoflush and never commit. Existing archived-project read rules
    are retained, and old versions remain addressable when explicitly requested.
    """

    def __init__(self, session_factory, actor_id: str):
        self.session_factory = session_factory
        self.actor_id = actor_id

    def _lookup(self, context, reference, kind):
        from fastapi import HTTPException
        from backend.creative_checker import services
        from backend.creative_checker.models import Asset, AssetVersion, Document, FileRecord, User

        if context.actor_id != self.actor_id:
            _fail("FILE_ACCESS_DENIED")
        version_id = reference.document_version_id if kind == "pdf" else reference.asset_version_id
        expected = context.document_version_id if kind == "pdf" else context.asset_version_id
        if expected != version_id:
            _fail("FILE_REFERENCE_MISMATCH")
        try:
            with self.session_factory() as db:
                with db.no_autoflush:
                    user = db.get(User, self.actor_id, populate_existing=True)
                    if user is None or not user.active:
                        _fail("FILE_ACCESS_DENIED")
                    model = Document if kind == "pdf" else AssetVersion
                    version, project, _role = services.object_access(db, model, version_id, user)
                    if (version.id != version_id or project.id != context.project_id
                            or version.project_id != project.id or version.file_id != reference.file_id):
                        _fail("FILE_REFERENCE_MISMATCH")
                    file, file_project, _role = services.object_access(db, FileRecord, version.file_id, user)
                    if file.id != reference.file_id or file.project_id != project.id or file_project.id != project.id:
                        _fail("FILE_REFERENCE_MISMATCH")
                    width = height = None
                    if kind == "pdf":
                        if file.media_type != "application/pdf":
                            _fail("FILE_SOURCE_UNSUPPORTED")
                    else:
                        asset, asset_project, _role = services.object_access(db, Asset, version.asset_id, user)
                        if asset.project_id != project.id or asset_project.id != project.id:
                            _fail("FILE_REFERENCE_MISMATCH")
                        if asset.kind != "poster" or file.media_type not in {"image/png", "image/jpeg", "image/webp"}:
                            _fail("FILE_SOURCE_UNSUPPORTED")
                        measurements = version.media_metadata
                        if not isinstance(measurements, dict) or measurements.get("actual_mime") != file.media_type:
                            _fail("FILE_METADATA_INVALID")
                        # Existing inspect_media records EXIF-oriented dimensions.
                        width = _positive_integer(measurements.get("width"))
                        height = _positive_integer(measurements.get("height"))
                    return StoredFileMetadata(
                        file_id=file.id, project_id=file.project_id, version_id=version.id,
                        kind=kind, storage_key=_creative_key(file.storage_key, project.id),
                        media_type=file.media_type, byte_size=_positive_integer(file.size),
                        sha256=file.sha256, width=width, height=height, coordinate_space="display",
                    )
        except AdapterFailure:
            raise
        except HTTPException as error:
            _authorize_error(error)
        except (ValueError, TypeError, KeyError):
            _fail("FILE_METADATA_INVALID")
        except Exception:
            _fail("FILE_STORAGE_UNAVAILABLE")

    def lookup_document(self, context: Context, reference: DocumentRef) -> StoredFileMetadata:
        # cc_documents.id identifies a particular immutable document version;
        # its integer version column is not the framework's version identifier.
        return self._lookup(context, reference, "pdf")

    def lookup_asset(self, context: Context, reference: AssetRef) -> StoredFileMetadata:
        return self._lookup(context, reference, "poster")


class CreativeCheckerStorageReader:
    """Read through an already created LocalStorage/S3Storage instance."""

    def __init__(self, storage):
        self.storage = storage

    def read(self, metadata: StoredFileMetadata, maximum: int) -> bytes:
        try:
            maximum = _maximum(maximum)
            key = _creative_key(metadata.storage_key, metadata.project_id)
            # Existing read uses an inclusive end offset, so this asks for at
            # most maximum + 1 bytes without normalizing or replacing originals.
            raw = self.storage.read(key, 0, maximum)
            if not isinstance(raw, bytes) or len(raw) > maximum + 1:
                _fail("FILE_STORAGE_UNAVAILABLE")
            return raw
        except AdapterFailure:
            raise
        except FileNotFoundError:
            _fail("FILE_UNAVAILABLE")
        except Exception:
            _fail("FILE_STORAGE_UNAVAILABLE")


class LegacyPosterCatalog:
    """Reuse M008 image metadata without adding a PDF schema or new storage.

    authorization and subject are the existing authenticated application objects.
    File IDs select original versus display bytes explicitly; their hashes and
    dimensions are never mixed. Original dimensions describe encoded pixels.
    """

    def __init__(self, engine, authorization, subject, developer_allowed=False):
        self.engine = engine
        self.authorization = authorization
        self.subject = subject
        self.developer_allowed = developer_allowed

    def lookup_document(self, context: Context, reference: DocumentRef) -> StoredFileMetadata:
        _fail("FILE_SOURCE_UNSUPPORTED")

    def lookup_asset(self, context: Context, reference: AssetRef) -> StoredFileMetadata:
        from backend.app.security.errors import AuthError
        from backend.app.storage.local import key

        if context.actor_id != str(getattr(self.subject, "user_id", "")):
            _fail("FILE_ACCESS_DENIED")
        if context.asset_version_id != reference.asset_version_id:
            _fail("FILE_REFERENCE_MISMATCH")
        try:
            pid, aid, fid = UUID(context.project_id), UUID(reference.asset_version_id), UUID(reference.file_id)
            with self.engine.connect() as connection:
                options = {"expected_project": pid, "developer_allowed": self.developer_allowed}
                image = self.authorization.resource(connection, self.subject, "asset_versions", aid, **options)
                if str(image["project_id"]) != context.project_id or str(image["asset_version_id"]) != reference.asset_version_id:
                    _fail("FILE_REFERENCE_MISMATCH")
                if fid == image["original_file_id"]:
                    representation = "original"
                elif fid == image["display_file_id"]:
                    representation = "display"
                else:
                    _fail("FILE_REFERENCE_MISMATCH")
                file = self.authorization.resource(connection, self.subject, "stored_files", fid, **options)
                if str(file["project_id"]) != context.project_id or str(file["file_id"]) != reference.file_id:
                    _fail("FILE_REFERENCE_MISMATCH")
                if file["state"] != "stored":
                    _fail("FILE_UNAVAILABLE")
                if (file["storage_backend"] != "local" or file["storage_key"] != key(pid, fid)
                        or file["file_role"] != representation + "_image"):
                    _fail("FILE_METADATA_INVALID")
                if representation == "original":
                    if file["sha256"] != image["original_sha256"] or file["parent_file_id"] is not None:
                        _fail("FILE_METADATA_INVALID")
                    if file["detected_mime"] not in {"image/png", "image/jpeg", "image/webp"}:
                        _fail("FILE_SOURCE_UNSUPPORTED")
                elif file["detected_mime"] != "image/png" or file["parent_file_id"] != image["original_file_id"]:
                    _fail("FILE_METADATA_INVALID")
                return StoredFileMetadata(
                    file_id=reference.file_id, project_id=context.project_id, version_id=reference.asset_version_id,
                    kind="poster", storage_key=file["storage_key"], media_type=file["detected_mime"],
                    byte_size=_positive_integer(file["byte_size"]), sha256=file["sha256"],
                    width=_positive_integer(image[representation + "_width"]),
                    height=_positive_integer(image[representation + "_height"]),
                    coordinate_space="encoded" if representation == "original" else "display",
                )
        except AdapterFailure:
            raise
        except AuthError as error:
            _authorize_error(error)
        except (ValueError, TypeError, KeyError):
            _fail("FILE_METADATA_INVALID")
        except Exception:
            _fail("FILE_STORAGE_UNAVAILABLE")


class LegacyStorageReader:
    """Use the existing M008 verified stream; storage lifetime stays with the app."""

    def __init__(self, storage):
        self.storage = storage

    def read(self, metadata: StoredFileMetadata, maximum: int) -> bytes:
        from backend.app.storage.local import StorageError, key

        try:
            maximum = _maximum(maximum)
            if metadata.storage_key != key(metadata.project_id, metadata.file_id):
                _fail("FILE_REFERENCE_MISMATCH")
            with self.storage.verified(metadata.storage_key, metadata.byte_size, metadata.sha256) as stream:
                raw = stream.read(maximum + 1)
            if not isinstance(raw, bytes) or len(raw) > maximum + 1:
                _fail("FILE_STORAGE_UNAVAILABLE")
            return raw
        except AdapterFailure:
            raise
        except FileNotFoundError:
            _fail("FILE_UNAVAILABLE")
        except StorageError as error:
            _fail("FILE_INTEGRITY_MISMATCH" if error.code == "STORAGE_CORRUPT" else "FILE_STORAGE_UNAVAILABLE")
        except Exception:
            _fail("FILE_STORAGE_UNAVAILABLE")
