"""Bounded, text-only document extraction. Input files are never modified."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import PurePosixPath
from typing import Any

from defusedxml import ElementTree as SafeET

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_PAGES = 50
MAX_CHARACTERS = 100_000
MAX_ZIP_ENTRIES = 2048
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class DocumentError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class _Blocks(list):
    character_count = 0


def _xml(data: bytes):
    try:
        return SafeET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception as exc:
        raise DocumentError("invalid_xml", "문서 XML이 잘못되었거나 허용하지 않는 선언을 포함합니다.") from exc


def _archive(data: bytes) -> dict[str, bytes]:
    """Validate all members, including unused ones; never extract archive paths."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise DocumentError("zip_entry_limit", "문서 내부 파일 수가 한도를 넘습니다.")
            total = 0
            seen: set[str] = set()
            members: dict[str, bytes] = {}
            for info in entries:
                # ZipInfo.filename normalizes Windows separators and drops NUL tails.
                # Validate the original central-directory name before using its paths.
                name = info.orig_filename
                path = PurePosixPath(name)
                if (
                    not name or "\\" in name or "\x00" in name or ":" in name
                    or path.is_absolute() or ".." in path.parts
                    or any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))
                ):
                    raise DocumentError("unsafe_zip_path", "문서 내부에 안전하지 않은 경로가 있습니다.")
                if name.casefold() in seen:
                    raise DocumentError("duplicate_zip_path", "문서 내부 경로가 중복됩니다.")
                seen.add(name.casefold())
                if info.flag_bits & 1:
                    raise DocumentError("encrypted_document", "암호화된 문서는 지원하지 않습니다.")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise DocumentError("unsafe_zip_path", "문서 내부 심볼릭 링크는 허용하지 않습니다.")
                total += info.file_size
                if total > MAX_DOCUMENT_BYTES or info.file_size < 0:
                    raise DocumentError("expanded_size_limit", "문서의 압축 해제 총량은 20MiB 이하여야 합니다.")
                if info.is_dir():
                    continue
                with archive.open(info) as stream:
                    payload = stream.read(min(MAX_DOCUMENT_BYTES + 1, info.file_size + 1))
                if len(payload) != info.file_size:
                    raise DocumentError("invalid_archive", "문서 내부 파일 크기가 올바르지 않습니다.")
                members[name] = payload
            return members
    except DocumentError:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError, EOFError) as exc:
        raise DocumentError("invalid_archive", "올바른 DOCX/HWPX 압축 문서가 아닙니다.") from exc


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(element, *, omit_tables: bool = True) -> str:
    parts: list[str] = []

    def visit(node):
        name = _local(node.tag)
        if omit_tables and name == "tbl":
            return
        if name in ("t", "text") and node.text:
            parts.append(node.text)
        elif name == "tab":
            parts.append("\t")
        elif name in ("br", "cr", "lineBreak"):
            parts.append("\n")
        for child in node:
            visit(child)

    visit(element)
    return "".join(parts).strip()


def _append(blocks: list[dict], text: str, *, page=None, kind="paragraph", **extra):
    if not text.strip():
        return
    count = blocks.character_count
    if count + len(text) > MAX_CHARACTERS:
        raise DocumentError("character_limit", "문서 추출량은 10만 자 이하여야 합니다.")
    blocks.append({"id": f"b{len(blocks) + 1}", "page": page, "kind": kind, "text": text, **extra})
    blocks.character_count = count + len(text)


def _table(element, *, word: bool) -> dict[str, Any]:
    rows = []
    for row in element:
        if _local(row.tag) != "tr":
            continue
        cells = []
        for cell in row:
            if _local(cell.tag) != "tc":
                continue
            paragraphs = [node for node in cell.iter() if _local(node.tag) == "p"]
            cells.append("\n".join(value for node in paragraphs if (value := _text(node))))
        rows.append(cells)
    text = "\n".join("\t".join(row) for row in rows)
    return {"text": text, "table": {"rows": rows, "row_count": len(rows), "column_count": max(map(len, rows), default=0)}}


def _walk_xml(element, blocks: list[dict], warnings: list[str], *, word: bool):
    name = _local(element.tag)
    if name == "tbl":
        table = _table(element, word=word)
        _append(blocks, table["text"], kind="table", table=table["table"])
        if any(_local(node.tag) == "tbl" for child in element for node in child.iter()):
            warnings.append("nested_table_layout_not_preserved")
        return
    if name == "p":
        _append(blocks, _text(element))
        # A paragraph can contain a table in HWPX. Do not count its text as prose.
        def visit_tables(node):
            for child in node:
                if _local(child.tag) == "tbl":
                    _walk_xml(child, blocks, warnings, word=word)
                else:
                    visit_tables(child)
        visit_tables(element)
        return
    for child in element:
        _walk_xml(child, blocks, warnings, word=word)


def _extract_docx(data: bytes):
    files = _archive(data)
    if "word/document.xml" not in files or "[Content_Types].xml" not in files:
        raise DocumentError("invalid_docx", "DOCX 필수 구성 파일이 없습니다.")
    types = _xml(files["[Content_Types].xml"])
    if not any(
        node.attrib.get("PartName") == "/word/document.xml"
        and node.attrib.get("ContentType") == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
        for node in types
    ):
        raise DocumentError("invalid_docx", "DOCX 본문 형식이 올바르지 않습니다.")
    root = _xml(files["word/document.xml"])
    if root.tag != f"{{{WORD_NS}}}document":
        raise DocumentError("invalid_docx", "DOCX 본문 네임스페이스가 올바르지 않습니다.")
    body = root.find(f"{{{WORD_NS}}}body")
    if body is None:
        raise DocumentError("invalid_docx", "DOCX 본문이 없습니다.")
    blocks = _Blocks()
    warnings = ["page_numbers_and_coordinates_unavailable", "headers_footers_drawings_not_extracted"]
    _walk_xml(body, blocks, warnings, word=True)
    return blocks, warnings, None


def _extract_hwpx(data: bytes):
    files = _archive(data)
    if files.get("mimetype", b"").strip() != b"application/hwp+zip" or "Contents/content.hpf" not in files:
        raise DocumentError("invalid_hwpx", "HWPX 필수 구성 파일 또는 MIME 형식이 올바르지 않습니다.")
    manifest = _xml(files["Contents/content.hpf"])
    sections = sorted(
        (name for name in files if re.fullmatch(r"Contents/section\d+\.xml", name)),
        key=lambda name: int(re.search(r"section(\d+)", name).group(1)),
    )
    if not sections or "encryption" in files or any("encrypt" in _local(node.tag).lower() for node in manifest.iter()):
        raise DocumentError("invalid_or_encrypted_hwpx", "암호화되었거나 본문 구역이 없는 HWPX 문서입니다.")
    blocks = _Blocks()
    warnings = ["page_numbers_and_coordinates_unavailable", "headers_footers_drawings_not_extracted"]
    for name in sections:
        root = _xml(files[name])
        if _local(root.tag) != "sec" or not root.tag.startswith("{http://www.hancom.co.kr/hwpml/"):
            raise DocumentError("invalid_hwpx", "HWPX 구역 XML 형식이 올바르지 않습니다.")
        _walk_xml(root, blocks, warnings, word=False)
    return blocks, warnings, None


def _extract_pdf(data: bytes):
    if not data.startswith(b"%PDF-"):
        raise DocumentError("invalid_pdf", "PDF 파일 시그니처가 올바르지 않습니다.")
    import pymupdf

    blocks = _Blocks()
    warnings: list[str] = []
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.needs_pass or document.is_encrypted:
                raise DocumentError("encrypted_document", "암호화된 PDF는 지원하지 않습니다.")
            pages = document.page_count
            if pages < 1 or pages > MAX_PAGES:
                raise DocumentError("page_limit", "PDF는 1~50페이지여야 합니다.")
            for number, page in enumerate(document, 1):
                has_text = False
                # Text extraction only: no OCR, rendering, or inferred bounding boxes.
                for item in page.get_text("dict", sort=True, flags=pymupdf.TEXTFLAGS_TEXT)["blocks"]:
                    if item.get("type") != 0:
                        continue
                    text = "\n".join("".join(span.get("text", "") for span in line["spans"]) for line in item["lines"]).strip()
                    if text:
                        has_text = True
                        _append(blocks, text, page=number, bbox=list(item["bbox"]))
                if not has_text:
                    if page.get_images():
                        raise DocumentError("scanned_pdf_unsupported", "텍스트가 없는 스캔 PDF 페이지는 지원하지 않습니다.")
                    warnings.append(f"page_{number}_has_no_extractable_text")
                elif page.get_images():
                    warnings.append(f"page_{number}_embedded_images_not_read")
            return blocks, warnings, pages
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("invalid_pdf", "PDF 본문을 읽을 수 없습니다.") from exc


def extract_document(data: bytes, filename: str) -> dict:
    """Extract PDF points or XML paragraphs/tables; XML page/bbox values stay unknown."""
    if not isinstance(data, bytes) or not data:
        raise DocumentError("empty_document", "문서 내용이 비어 있습니다.")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentError("document_size_limit", "문서는 20MiB 이하여야 합니다.")
    if not isinstance(filename, str) or not filename:
        raise DocumentError("invalid_filename", "문서 파일명이 필요합니다.")
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if suffix not in (".pdf", ".docx", ".hwpx"):
        raise DocumentError("unsupported_document", "PDF, DOCX, HWPX 문서만 지원합니다.")
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        raise DocumentError("encrypted_or_legacy_document", "암호화되었거나 구형 Office/HWP 형식인 문서는 지원하지 않습니다.")
    extractors = {".pdf": _extract_pdf, ".docx": _extract_docx, ".hwpx": _extract_hwpx}
    blocks, warnings, pages = extractors[suffix](data)
    if not blocks:
        raise DocumentError("no_extractable_text", "추출할 수 있는 본문 텍스트가 없습니다. 스캔 문서는 지원하지 않습니다.")
    return {
        "blocks": blocks,
        "warnings": list(dict.fromkeys(warnings)),
        "metadata": {
            "format": suffix[1:], "filename": filename, "size_bytes": len(data),
            "page_count": pages, "block_count": len(blocks),
            "character_count": sum(len(block["text"]) for block in blocks),
            "coordinate_space": "pdf_points_xyxy" if suffix == ".pdf" else None,
        },
    }
