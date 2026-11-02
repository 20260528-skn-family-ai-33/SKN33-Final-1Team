"""Configured full-flow demo using generated real files and fixture callbacks.

Explicit execution only. Uses isolated SQLite, existing store_file/LocalStorage,
PDF text extraction and a deterministic pixel counter, not Qwen or RAG. The
ContextVar is a scoped dependency seam for this demo's local factories only.
Importing this module creates no files, database connections or model clients.
"""
from __future__ import annotations

from contextvars import ContextVar
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory

_files = ContextVar("file_flow_demo_access")


def _extract(request, document):
    import pymupdf
    from backend.agent_framework.contracts import CriteriaDraft, Criterion, SourceRef

    with pymupdf.open(stream=document.content, filetype="pdf") as pdf:
        quote = pdf[0].get_text().strip()
    if quote != "Display exactly 3 green marks.":
        raise ValueError("unexpected generated fixture")
    source = SourceRef(document_version_id=request.document.document_version_id,
        page=1, block_id="generated-line-1", quote=quote, source_hash=sha256(quote.encode()).hexdigest())
    return CriteriaDraft(context=request.context, synthetic=True,
        warnings=("Generated fixture; PDF text extraction and pixel counting, not Qwen inference.",),
        criteria=(Criterion(criterion_id="green-marks", text=quote, kind="number", expected="3",
                            operator="equals", unit="개", source=source),))


def _inspect(request, poster):
    from PIL import Image
    from backend.agent_framework.contracts import ImageEvidence, InspectionResult, Observation

    with Image.open(BytesIO(poster.content)) as image:
        image = image.convert("RGB")
        count = sum(image.getpixel((x, 12)) == (49, 91, 72) for x in (8, 16, 24))
    criterion = request.criteria[0]
    citations = tuple(e.evidence_id for e in request.evidence.evidence if criterion.criterion_id in e.criterion_ids)
    return InspectionResult(context=request.context, synthetic=True, observations=(Observation(
        criterion_id=criterion.criterion_id, observed=str(count), verdict="pass" if count == 3 else "fail",
        evidence_ids=citations, image=ImageEvidence(asset_version_id=request.asset.asset_version_id,
            asset_hash=request.asset.sha256, bbox=(0, 0, 1, 1))),))


def create_extractor():
    from backend.agent_framework.adapters.file_backed import FileBackedExtractor
    return FileBackedExtractor(_files.get(), _extract)


def create_inspector():
    from backend.agent_framework.adapters.file_backed import FileBackedInspector
    return FileBackedInspector(_files.get(), _inspect)


def run_demo():
    import pymupdf
    from PIL import Image
    from backend.creative_checker.database import Base, create_engine, session_factory
    from backend.creative_checker.models import Asset, AssetVersion, Document, Membership, Project, User, new_id
    from backend.creative_checker.tool_records import ToolCall  # noqa: F401 — complete existing metadata
    from backend.creative_checker.services import commit, store_file
    from backend.creative_checker.storage import LocalStorage
    from backend.agent_framework.adapters.project_files import CreativeCheckerCatalog, CreativeCheckerStorageReader
    from backend.agent_framework.composition import RoleBinding, TeamConfiguration, load_config
    from backend.agent_framework.contracts import AssetRef, Context, DocumentRef
    from backend.agent_framework.files import VerifiedFileAccess
    from backend.agent_framework.flow_checks import FlowCheckCase, load_flow_case, run_flow_check

    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((36, 60), "Display exactly 3 green marks.")
        pdf_bytes = pdf.tobytes()

    def poster_bytes(count):
        stream = BytesIO()
        with Image.new("RGB", (32, 24), "white") as image:
            for x in (8, 16, 24)[:count]:
                image.putpixel((x, 12), (49, 91, 72))
            image.save(stream, format="PNG")
        return stream.getvalue()

    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)  # Isolated demo DB only; never product startup.
        sessions = session_factory(engine)
        with TemporaryDirectory(prefix="cc-file-flow-") as temporary:
            root = Path(temporary)
            storage = LocalStorage(root / "files")
            with sessions() as db:
                user = User(id=new_id(), email="flow-fixture@example.test", name="Flow fixture",
                            password_hash="disabled-demo-account", active=True)
                db.add(user); db.flush()
                project = Project(id=new_id(), name="Isolated flow fixture", created_by=user.id)
                db.add(project); db.flush()
                db.add(Membership(project_id=project.id, user_id=user.id, role="editor"))
                document_file = store_file(db, storage, project, user, "requirements.pdf", pdf_bytes, "application/pdf")
                document = Document(id=new_id(), project_id=project.id, version=1,
                    file_id=document_file.id, filename="requirements.pdf", created_by=user.id)
                asset = Asset(id=new_id(), project_id=project.id, name="Green marks", kind="poster", created_by=user.id)
                db.add_all([document, asset]); db.flush()
                versions = []
                for index, count in enumerate((2, 3), 1):
                    file = store_file(db, storage, project, user, f"poster-v{index}.png", poster_bytes(count), "image/png")
                    version = AssetVersion(id=new_id(), project_id=project.id, asset_id=asset.id, version=index,
                        file_id=file.id, filename=f"poster-v{index}.png", created_by=user.id,
                        media_metadata={"actual_mime": "image/png", "width": 32, "height": 24})
                    db.add(version); db.flush()
                    versions.append(AssetRef(file_id=file.id, asset_version_id=version.id,
                                             sha256=file.sha256, width=32, height=24))
                context = Context(project_id=project.id, actor_id=user.id, run_id="generated-flow-1",
                    document_version_id=document.id, criteria_version_id="generated-criteria",
                    asset_version_id=versions[0].asset_version_id)
                revised = context.model_copy(update={"run_id": "generated-flow-2", "parent_run_id": context.run_id,
                                                     "asset_version_id": versions[1].asset_version_id})
                case = FlowCheckCase(id="generated-file-flow", context=context,
                    document=DocumentRef(file_id=document_file.id, document_version_id=document.id,
                        sha256=document_file.sha256, page_count=1), asset=versions[0],
                    revision_context=revised, revision_asset=versions[1], expected_initial_verdict="fail",
                    change_reason="Generated fixture: replace two marks with three, then review the entire new version.")
                actor = user.id
                commit(db)
            access = VerifiedFileAccess(CreativeCheckerCatalog(sessions, actor), CreativeCheckerStorageReader(storage))
            module = "examples.agent_framework.file_flow_demo"
            config = TeamConfiguration(roles={
                "A02": RoleBinding(kind="factory", factory=f"{module}:create_extractor"),
                "A03": RoleBinding(kind="synthetic"),
                "A04_A05": RoleBinding(kind="factory", factory=f"{module}:create_inspector"),
                "A06": RoleBinding(kind="reference"), "A07": RoleBinding(kind="synthetic"),
            })
            (root / "config.json").write_text(config.model_dump_json(), encoding="utf-8")
            (root / "workflow.json").write_text(case.model_dump_json(), encoding="utf-8")
            # Use the importable module's dependency scope even when invoked via -m.
            import importlib
            factories = importlib.import_module(module)
            token = factories._files.set(access)
            try:
                report = run_flow_check(load_flow_case(root / "workflow.json"), load_config(root / "config.json"))
            finally:
                factories._files.reset(token)
            return {"generated_test_media": True, "database": "isolated SQLite", "model_calls": 0,
                    "operational_database_checked": False, "workflow": report,
                    "notice": "Existing storage reads and deterministic PDF/pixel fixture callbacks; no Qwen, Neo4j or authenticated human approval."}
    finally:
        engine.dispose()


if __name__ == "__main__":
    result = run_demo()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["workflow"]["passed"] else 1)
