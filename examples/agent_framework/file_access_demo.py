"""Read generated PDF/poster bytes through the existing product file service.

Run explicitly with python -m examples.agent_framework.file_access_demo.
Only an isolated SQLite database and a temporary local store are created. No
environment configuration, operational database, model, or service is opened.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory


def run_demo() -> dict:
    import pymupdf
    from PIL import Image

    from backend.creative_checker.database import Base, create_engine, session_factory
    from backend.creative_checker.models import (
        Asset, AssetVersion, Document, Membership, Project, User, new_id,
    )
    from backend.creative_checker.tool_records import ToolCall  # noqa: F401 — complete existing metadata
    from backend.creative_checker.services import commit, store_file
    from backend.creative_checker.storage import LocalStorage
    from backend.agent_framework.adapters.project_files import CreativeCheckerCatalog, CreativeCheckerStorageReader
    from backend.agent_framework.contracts import AssetRef, Context, DocumentRef
    from backend.agent_framework.files import VerifiedFileAccess
    from backend.agent_framework.ports import AdapterFailure

    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((36, 60), "Generated file-access fixture. Not a model evaluation.")
        pdf_bytes = pdf.tobytes()
    image_bytes = BytesIO()
    with Image.new("RGB", (32, 24), "#315b48") as image:
        image.save(image_bytes, format="PNG")
    poster_bytes = image_bytes.getvalue()

    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)  # Explicit isolated demo only; never product startup.
        sessions = session_factory(engine)
        with TemporaryDirectory(prefix="cc-file-access-") as folder:
            storage = LocalStorage(Path(folder) / "files")
            with sessions() as db:
                user = User(id=new_id(), email="file-demo@example.test", name="File fixture",
                            password_hash="disabled-demo-account", active=True)
                db.add(user); db.flush()
                project = Project(id=new_id(), name="Isolated file fixture", created_by=user.id)
                db.add(project); db.flush()
                db.add(Membership(project_id=project.id, user_id=user.id, role="editor"))
                document_file = store_file(db, storage, project, user, "requirements.pdf", pdf_bytes, "application/pdf")
                poster_file = store_file(db, storage, project, user, "poster.png", poster_bytes, "image/png")
                document = Document(id=new_id(), project_id=project.id, version=1,
                                    file_id=document_file.id, filename="requirements.pdf", created_by=user.id)
                asset = Asset(id=new_id(), project_id=project.id, name="Poster", kind="poster", created_by=user.id)
                db.add_all([document, asset]); db.flush()
                version = AssetVersion(id=new_id(), project_id=project.id, asset_id=asset.id, version=1,
                                       file_id=poster_file.id, filename="poster.png", created_by=user.id,
                                       media_metadata={"actual_mime": "image/png", "width": 32, "height": 24})
                db.add(version); db.flush()
                context = Context(project_id=project.id, actor_id=user.id, run_id="file-access-demo",
                                  document_version_id=document.id, criteria_version_id="demo-criteria",
                                  asset_version_id=version.id)
                doc_ref = DocumentRef(file_id=document_file.id, document_version_id=document.id,
                                      sha256=document_file.sha256, page_count=1)
                poster_ref = AssetRef(file_id=poster_file.id, asset_version_id=version.id,
                                      sha256=poster_file.sha256, width=32, height=24)
                actor_id = user.id
                commit(db)
            access = VerifiedFileAccess(CreativeCheckerCatalog(sessions, actor_id), CreativeCheckerStorageReader(storage))
            pdf = access.read_document(context, doc_ref)
            poster = access.read_asset(context, poster_ref)
            assert pdf.content == pdf_bytes and poster.content == poster_bytes
            try:
                access.read_document(context, doc_ref.model_copy(update={"sha256": "0" * 64}))
            except AdapterFailure as error:
                rejection = error.code
            else:
                raise AssertionError("A changed file hash was accepted")
            return {
                "generated_test_media": True, "database": "isolated SQLite",
                "storage": "existing creative_checker.LocalStorage via services.store_file",
                "model_calls": 0, "operational_database_checked": False,
                "pdf": {"byte_size": pdf.byte_size, "page_count": pdf.page_count,
                        "sha256": sha256(pdf.content).hexdigest(), "hash_verified": True},
                "poster": {"byte_size": poster.byte_size, "width": poster.width, "height": poster.height,
                           "sha256": sha256(poster.content).hexdigest(), "hash_verified": True},
                "changed_reference_rejected": rejection,
                "notice": "Generated real file bytes and test identity; not Qwen inference, production DB or end-user authentication verification.",
            }
    finally:
        engine.dispose()


if __name__ == "__main__":
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))
