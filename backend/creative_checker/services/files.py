"""Product files operations."""

import hashlib
import io
from pathlib import PurePosixPath

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.creative_checker.models import FileRecord, Project, User, new_id
from backend.creative_checker.services.common import fail


def clean_filename(filename: str | None) -> str:
    if not filename or len(filename) > 255 or filename in {".", ".."} or any(c in filename for c in ("/", "\\", ":", "\x00", "\r", "\n")):
        fail(422, "invalid_filename", "A plain filename without a path is required")
    if any(ord(c) < 32 or ord(c) == 127 for c in filename):
        fail(422, "invalid_filename", "Filename contains control characters")
    return filename


def validate_document(data: bytes, filename: str) -> str:
    """Check the actual container; text extraction remains the document job's work."""
    import zipfile

    if not data or len(data) > 20 * 1024 * 1024:
        fail(422, "document_size_limit", "Documents must contain between 1 byte and 20MiB")
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            fail(422, "invalid_pdf", "The uploaded file is not a PDF")
        import pymupdf
        try:
            with pymupdf.open(stream=data, filetype="pdf") as pdf:
                if pdf.needs_pass or pdf.is_encrypted or not 1 <= pdf.page_count <= 50:
                    fail(422, "invalid_pdf", "PDF must be unencrypted and contain 1–50 pages")
        except HTTPException:
            raise
        except Exception:
            fail(422, "invalid_pdf", "The PDF container could not be read")
        return "application/pdf"
    if suffix not in {".docx", ".hwpx"}:
        fail(422, "unsupported_document", "PDF, DOCX and HWPX documents are supported")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2048 or sum(x.file_size for x in entries) > 20 * 1024 * 1024:
                fail(422, "invalid_archive", "Document archive exceeds its extraction limit")
            names = set()
            for entry in entries:
                parts = entry.filename.rstrip("/").split("/")
                if entry.flag_bits & 1 or any(p in {"", ".", ".."} for p in parts) or any(c in entry.filename for c in ("\\", ":", "\x00")):
                    fail(422, "invalid_archive", "Document archive contains an encrypted or unsafe member")
                if entry.filename.casefold() in names or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    fail(422, "invalid_archive", "Document archive contains duplicate or linked members")
                names.add(entry.filename.casefold())
            if suffix == ".docx" and not {"word/document.xml", "[Content_Types].xml"}.issubset(archive.namelist()):
                fail(422, "invalid_docx", "DOCX required members are missing")
            if suffix == ".hwpx" and ("Contents/content.hpf" not in archive.namelist() or "mimetype" not in archive.namelist()
                                       or archive.read("mimetype") != b"application/hwp+zip"):
                fail(422, "invalid_hwpx", "HWPX required members are missing")
    except HTTPException:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError):
        fail(422, "invalid_archive", "The document archive could not be read")
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if suffix == ".docx" else "application/hwp+zip"


def store_file(db: Session, storage, project: Project, user: User, filename: str, data: bytes, media_type: str) -> FileRecord:
    file = FileRecord(id=new_id(), project_id=project.id, storage_key=f"{project.id}/{new_id()}",
                      media_type=media_type, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                      filename=filename, created_by=user.id)
    storage.put(file.storage_key, data, media_type)
    db.info.setdefault("pending_files", []).append((storage, file.storage_key))
    db.add(file)
    db.flush()
    return file


def cleanup_files(db: Session) -> None:
    for storage, key in db.info.pop("pending_files", []):
        try:
            storage.delete(key)
        except Exception:
            # A failed storage cleanup must not hide the original transaction error.
            import logging
            logging.getLogger(__name__).error("Uncommitted file cleanup failed")


def commit(db: Session) -> None:
    db.commit()
    db.info.pop("pending_files", None)
