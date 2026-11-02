"""Existing catalogs/stores are read through adapters; no operational DB is used."""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, update

from backend.agent_framework.adapters.project_files import (
    CreativeCheckerCatalog, CreativeCheckerStorageReader, LegacyPosterCatalog, LegacyStorageReader,
)
from backend.agent_framework.contracts import AssetRef, Context, DocumentRef
from backend.agent_framework.files import StoredFileMetadata, VerifiedFileAccess
from backend.agent_framework.ports import AdapterFailure


@pytest.fixture
def product(tmp_path):
    import pymupdf
    from PIL import Image
    from backend.creative_checker.database import Base, create_engine, session_factory
    from backend.creative_checker.models import Asset, AssetVersion, Document, FileRecord, Membership, Project, User, new_id
    from backend.creative_checker.storage import LocalStorage
    from backend.creative_checker.tool_records import ToolCall  # Register existing receipt foreign keys.

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = session_factory(engine)
    storage = LocalStorage(tmp_path / "existing files")
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((30, 50), "A real PDF byte fixture, not a model response.")
        pdf_bytes = pdf.tobytes()
    buffer = BytesIO()
    image = Image.new("RGB", (12, 8), "white")
    exif = Image.Exif(); exif[274] = 6
    image.save(buffer, format="JPEG", exif=exif)
    image.close()
    poster_bytes = buffer.getvalue()
    with sessions.begin() as db:
        user = User(id=new_id(), email="member@example.test", name="Member", password_hash="unused", active=True)
        outsider = User(id=new_id(), email="outsider@example.test", name="Outsider", password_hash="unused", active=True)
        db.add_all([user, outsider]); db.flush()
        project = Project(id=new_id(), name="Files", created_by=user.id)
        foreign = Project(id=new_id(), name="Other", created_by=user.id)
        db.add_all([project, foreign]); db.flush()
        db.add_all([Membership(project_id=p.id, user_id=user.id, role="viewer") for p in (project, foreign)])
        pdf_file = FileRecord(id=new_id(), project_id=project.id, storage_key=project.id + "/document",
                             media_type="application/pdf", size=len(pdf_bytes), sha256=sha256(pdf_bytes).hexdigest(),
                             filename="requirements.pdf", created_by=user.id)
        image_file = FileRecord(id=new_id(), project_id=project.id, storage_key=project.id + "/poster",
                               media_type="image/jpeg", size=len(poster_bytes), sha256=sha256(poster_bytes).hexdigest(),
                               filename="poster.jpg", created_by=user.id)
        db.add_all([pdf_file, image_file]); db.flush()
        document = Document(id=new_id(), project_id=project.id, version=4, file_id=pdf_file.id,
                            filename=pdf_file.filename, created_by=user.id)
        asset = Asset(id=new_id(), project_id=project.id, name="Poster", kind="poster", created_by=user.id)
        db.add_all([document, asset]); db.flush()
        version = AssetVersion(id=new_id(), project_id=project.id, asset_id=asset.id, version=3,
                               file_id=image_file.id, filename=image_file.filename, created_by=user.id,
                               media_metadata={"actual_mime": "image/jpeg", "width": 8, "height": 12,
                                               "orientation_applied": True})
        db.add(version); db.flush()
        values = dict(actor=user.id, outsider=outsider.id, project=project.id, foreign=foreign.id,
                      document=document.id, asset=asset.id, version=version.id,
                      document_file=pdf_file.id, poster_file=image_file.id)
    storage.put(pdf_file.storage_key, pdf_bytes, pdf_file.media_type)
    storage.put(image_file.storage_key, poster_bytes, image_file.media_type)
    context = Context(project_id=values["project"], run_id="test-run", actor_id=values["actor"],
                      document_version_id=values["document"], criteria_version_id="criteria-1",
                      asset_version_id=values["version"])
    document_ref = DocumentRef(file_id=values["document_file"], document_version_id=values["document"],
                               sha256=sha256(pdf_bytes).hexdigest(), page_count=1)
    asset_ref = AssetRef(file_id=values["poster_file"], asset_version_id=values["version"],
                         sha256=sha256(poster_bytes).hexdigest(), media_type="image/jpeg", width=8, height=12)
    try:
        yield SimpleNamespace(engine=engine, sessions=sessions, storage=storage, ids=values, context=context,
                              document=document_ref, asset=asset_ref, pdf_bytes=pdf_bytes, poster_bytes=poster_bytes)
    finally:
        engine.dispose()


def catalog(product, actor=None):
    return CreativeCheckerCatalog(product.sessions, actor or product.ids["actor"])


def test_constructors_do_not_open_databases_or_initialize_storage():
    calls = []
    factory = lambda: calls.append("session")
    storage = SimpleNamespace(read=lambda *_: calls.append("read"), verified=lambda *_: calls.append("verified"))
    engine = SimpleNamespace(connect=lambda: calls.append("connect"))
    CreativeCheckerCatalog(factory, "bound-user")
    CreativeCheckerStorageReader(storage)
    LegacyPosterCatalog(engine, object(), object())
    LegacyStorageReader(storage)
    assert not calls


def test_existing_rebuild_metadata_storage_and_real_bytes_work_through_common_core(product):
    access = VerifiedFileAccess(catalog(product), CreativeCheckerStorageReader(product.storage))
    document = access.read_document(product.context, product.document)
    poster = access.read_asset(product.context, product.asset)
    assert document.content == product.pdf_bytes and document.page_count == 1
    assert poster.content == product.poster_bytes
    assert (poster.width, poster.height) == (8, 12)
    assert (poster.encoded_width, poster.encoded_height) == (12, 8)
    assert poster.exif_orientation == 6 and poster.coordinate_space == "display"
    # IDs come from version rows, not the integer 4/3 sequence numbers.
    assert document.reference.document_version_id == product.ids["document"]


def test_bound_actor_and_current_membership_are_required(product):
    with pytest.raises(AdapterFailure) as error:
        catalog(product).lookup_document(product.context.model_copy(update={"actor_id": product.ids["outsider"]}), product.document)
    assert error.value.code == "FILE_ACCESS_DENIED"
    with pytest.raises(AdapterFailure) as error:
        catalog(product, product.ids["outsider"]).lookup_document(
            product.context.model_copy(update={"actor_id": product.ids["outsider"]}), product.document)
    assert error.value.code == "FILE_ACCESS_DENIED"


@pytest.mark.parametrize("revocation", ("inactive_user", "removed_membership"))
def test_catalog_rechecks_access_in_each_new_session(product, revocation):
    from backend.creative_checker.models import Membership, User

    adapter = catalog(product)
    assert adapter.lookup_document(product.context, product.document).file_id == product.document.file_id
    with product.sessions.begin() as db:
        if revocation == "inactive_user":
            db.execute(update(User).where(User.id == product.ids["actor"]).values(active=False))
        else:
            db.execute(delete(Membership).where(Membership.user_id == product.ids["actor"],
                                                 Membership.project_id == product.ids["project"]))
    with pytest.raises(AdapterFailure) as error:
        adapter.lookup_document(product.context, product.document)
    assert error.value.code == "FILE_ACCESS_DENIED"


def test_existing_catalog_does_not_treat_request_actor_as_authentication():
    called = []
    adapter = CreativeCheckerCatalog(lambda: called.append(True), "bound-user")
    context = Context(project_id="p", run_id="r", actor_id="request-user", document_version_id="d", criteria_version_id="c")
    reference = DocumentRef(file_id="f", document_version_id="d", sha256="a" * 64, page_count=1)
    with pytest.raises(AdapterFailure) as error:
        adapter.lookup_document(context, reference)
    assert error.value.code == "FILE_ACCESS_DENIED" and called == []


@pytest.mark.parametrize("change", ("context_project", "reference_file", "context_version"))
def test_rebuild_catalog_binds_exact_project_file_and_version(product, change):
    context, reference = product.context, product.document
    if change == "context_project":
        context = context.model_copy(update={"project_id": product.ids["foreign"]})
    elif change == "reference_file":
        reference = reference.model_copy(update={"file_id": product.ids["poster_file"]})
    else:
        context = context.model_copy(update={"document_version_id": "other-version"})
    with pytest.raises(AdapterFailure) as error:
        catalog(product).lookup_document(context, reference)
    assert error.value.code == "FILE_REFERENCE_MISMATCH"


@pytest.mark.parametrize("change,expected", (("foreign_file_project", "FILE_REFERENCE_MISMATCH"),
    ("foreign_key", "FILE_REFERENCE_MISMATCH"), ("wrong_type", "FILE_SOURCE_UNSUPPORTED")))
def test_invalid_rebuild_file_metadata_cannot_cross_version_boundary(product, change, expected):
    from backend.creative_checker.models import FileRecord

    values = {"project_id": product.ids["foreign"]} if change == "foreign_file_project" else (
        {"storage_key": product.ids["foreign"] + "/document"} if change == "foreign_key" else {"media_type": "application/zip"})
    # Deliberately corrupt only this disposable SQLite fixture via SQL; production
    # ORM immutable-file guards remain untouched and no operational DB is used.
    with product.engine.begin() as connection:
        connection.execute(update(FileRecord.__table__).where(FileRecord.id == product.ids["document_file"]).values(**values))
    with pytest.raises(AdapterFailure) as error:
        catalog(product).lookup_document(product.context, product.document)
    assert error.value.code == expected


def test_rebuild_reader_reuses_bounded_existing_read_and_preserves_bytes(product):
    metadata = catalog(product).lookup_document(product.context, product.document)
    assert CreativeCheckerStorageReader(product.storage).read(metadata, 12) == product.pdf_bytes[:13]
    calls = []
    reader = CreativeCheckerStorageReader(SimpleNamespace(read=lambda *args: calls.append(args) or b"test"))
    assert reader.read(metadata, 3) == b"test"
    assert calls == [(metadata.storage_key, 0, 3)]


def test_storage_failure_messages_do_not_leak_backend_details(product):
    metadata = catalog(product).lookup_document(product.context, product.document)

    def fail(*args):
        raise RuntimeError("private-storage-path credential-token")

    with pytest.raises(AdapterFailure) as error:
        CreativeCheckerStorageReader(SimpleNamespace(read=fail)).read(metadata, 100)
    assert error.value.code == "FILE_STORAGE_UNAVAILABLE"
    assert "private-storage-path" not in str(error.value) and "credential-token" not in str(error.value)


def legacy():
    from backend.app.storage.local import key

    pid, uid, aid, original, display = (uuid4() for _ in range(5))
    original_bytes, display_bytes = b"original bytes", b"display bytes"
    image = dict(project_id=pid, asset_version_id=aid, original_file_id=original, display_file_id=display,
                 original_sha256=sha256(original_bytes).hexdigest(), original_width=12, original_height=8,
                 display_width=8, display_height=12)
    files = {}
    for fid, name, data, mime, parent in ((original, "original", original_bytes, "image/jpeg", None),
                                        (display, "display", display_bytes, "image/png", original)):
        files[fid] = dict(file_id=fid, project_id=pid, parent_file_id=parent, state="stored", storage_backend="local",
                          storage_key=key(pid, fid), file_role=name + "_image", byte_size=len(data),
                          sha256=sha256(data).hexdigest(), detected_mime=mime)
    calls = []

    @contextmanager
    def connect():
        calls.append("connect")
        yield object()

    def resource(connection, subject, table, identifier, **kwargs):
        assert kwargs == {"expected_project": pid, "developer_allowed": False}
        calls.append((table, identifier))
        return deepcopy(image if table == "asset_versions" else files[identifier])

    context = Context(project_id=str(pid), actor_id=str(uid), run_id="run", document_version_id="document",
                      criteria_version_id="criteria", asset_version_id=str(aid))
    subject = SimpleNamespace(user_id=uid)
    engine = SimpleNamespace(connect=connect)
    authorization = SimpleNamespace(resource=resource)
    adapter = LegacyPosterCatalog(engine, authorization, subject)
    references = {name: AssetRef(file_id=str(fid), asset_version_id=str(aid), sha256=files[fid]["sha256"],
                                 media_type=files[fid]["detected_mime"], width=image[name + "_width"],
                                 height=image[name + "_height"])
                  for name, fid in (("original", original), ("display", display))}
    return SimpleNamespace(catalog=adapter, context=context, image=image, files=files, calls=calls,
                           references=references, original=original, display=display, authorization=authorization)


def test_legacy_preserves_original_display_hash_and_coordinate_identity():
    harness = legacy()
    for name in ("original", "display"):
        reference = harness.references[name]
        result = harness.catalog.lookup_asset(harness.context, reference)
        assert (result.file_id, result.sha256, result.media_type) == (reference.file_id, reference.sha256, reference.media_type)
        assert (result.width, result.height) == (reference.width, reference.height)
        assert result.coordinate_space == ("encoded" if name == "original" else "display")
    assert harness.calls.count("connect") == 2


@pytest.mark.parametrize("fault,expected", (("missing", "FILE_UNAVAILABLE"),
    ("foreign_storage_key", "FILE_METADATA_INVALID"), ("wrong_parent", "FILE_METADATA_INVALID"),
    ("wrong_role", "FILE_METADATA_INVALID"), ("original_hash", "FILE_METADATA_INVALID")))
def test_legacy_catalog_checks_existing_file_link_state_and_metadata(fault, expected):
    harness = legacy()
    name = "original" if fault == "original_hash" else "display"
    row = harness.files[UUID(harness.references[name].file_id)]
    row.update({"state": "missing"} if fault == "missing" else
               {"storage_key": "objects/foreign/payload.bin"} if fault == "foreign_storage_key" else
               {"parent_file_id": uuid4()} if fault == "wrong_parent" else
               {"file_role": "original_image"} if fault == "wrong_role" else {"sha256": "0" * 64})
    with pytest.raises(AdapterFailure) as error:
        harness.catalog.lookup_asset(harness.context, harness.references[name])
    assert error.value.code == expected


def test_legacy_authorization_failure_is_preserved_without_backend_message():
    from backend.app.security.errors import AuthError

    harness = legacy()

    def denied(*args, **kwargs):
        raise AuthError("PROJECT_PERMISSION_DENIED", 403)

    harness.authorization.resource = denied
    with pytest.raises(AdapterFailure) as error:
        harness.catalog.lookup_asset(harness.context, harness.references["display"])
    assert error.value.code == "FILE_ACCESS_DENIED"


def test_legacy_pdf_is_explicitly_unsupported_without_database_access():
    harness = legacy()
    reference = DocumentRef(file_id="pdf", document_version_id="document", sha256="a" * 64, page_count=1)
    with pytest.raises(AdapterFailure) as error:
        harness.catalog.lookup_document(harness.context, reference)
    assert error.value.code == "FILE_SOURCE_UNSUPPORTED" and harness.calls == []


@pytest.mark.skipif(os.name != "posix", reason="Existing M008 LocalStorage requires POSIX directory descriptors")
def test_legacy_reader_reuses_real_verified_storage_and_does_not_normalize(tmp_path):
    from backend.app.storage.local import LocalStorage, key

    root = tmp_path / "legacy-store"
    LocalStorage.initialize(root)
    storage = LocalStorage(root)
    pid, fid, version = (str(uuid4()) for _ in range(3))
    data = b"existing immutable bytes"
    storage_key = key(pid, fid)
    storage.write_new(storage_key, data)
    metadata = StoredFileMetadata(file_id=fid, project_id=pid, version_id=version, kind="poster",
                                  storage_key=storage_key, media_type="image/png", byte_size=len(data),
                                  sha256=sha256(data).hexdigest(), width=1, height=1)
    try:
        reader = LegacyStorageReader(storage)
        assert reader.read(metadata, 8) == data[:9]
        with pytest.raises(AdapterFailure) as error:
            reader.read(metadata.model_copy(update={"sha256": "0" * 64}), 100)
        assert error.value.code == "FILE_INTEGRITY_MISMATCH"
    finally:
        storage.close()
