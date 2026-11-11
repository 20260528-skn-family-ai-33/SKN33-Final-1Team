"""Word(.docx) requirement documents -> UTF-8 text for the existing free-text A02 path.

Only ``word/document.xml`` is read, through defusedxml and explicit size limits.
No macro, Word/LibreOffice conversion, rendering, OCR or model call is involved,
so conversion takes milliseconds. The DOCX bytes themselves are not stored: the
registered immutable source is the converted text (AF004 ``text/plain``), and the
original DOCX name, size and SHA-256 are returned to the caller for comparison.

Body order is preserved. A paragraph becomes one line (manual line breaks start a
new line); a table row becomes one ``cell | cell`` line, and paragraphs inside a
cell are joined with `` / ``. Consecutive paragraphs form one block and each table
is its own block; blocks are separated by one blank line. Headers, footers,
footnotes, comments, automatic list numbers, deleted/hidden text and images are
not included. Text is otherwise copied verbatim (no normalization).
"""
from dataclasses import dataclass
from hashlib import sha256
import io
import re
import unicodedata
from typing import Literal
import zipfile

from pydantic import Field

from .contracts import Contract, Hash
from .documents import RegisteredDocument
from .ports import AdapterFailure
from .requirement_text import MAX_TEXT_CHARACTERS, inspect_text

DOCX_MEDIA_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
MAIN_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
CONVERTER = 'docx-text-v1'
MAX_DOCX_BYTES = 20 * 1024 * 1024
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 4000
MAX_DEPTH = 200
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
MC = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'
CT = '{http://schemas.openxmlformats.org/package/2006/content-types}'
# Containers that only wrap body/cell content (content controls, custom XML).
WRAPPERS = {W + 'sdt': W + 'sdtContent', W + 'customXml': None, W + 'smartTag': None}
# Accepted-view text: tracked deletions and the source of a tracked move are skipped.
SKIPPED = {W + 'del', W + 'moveFrom', W + 'delText', W + 'instrText', W + 'delInstrText',
           W + 'rPr', W + 'pPr', W + 'tblPr', W + 'trPr', W + 'tcPr', W + 'sectPr', MC + 'Fallback'}


@dataclass(frozen=True)
class DocxText:
    text: str
    blocks: int
    paragraphs: int
    table_rows: int


class DocxSource(Contract):
    media_type: Literal['application/vnd.openxmlformats-officedocument.wordprocessingml.document'] = DOCX_MEDIA_TYPE
    original_name: str = Field(min_length=1, max_length=251)
    byte_size: int = Field(gt=0, strict=True)
    sha256: Hash
    converter: Literal['docx-text-v1'] = CONVERTER
    blocks: int = Field(ge=1, strict=True)
    paragraphs: int = Field(ge=0, strict=True)
    table_rows: int = Field(ge=0, strict=True)
    characters: int = Field(ge=1, strict=True)


class ConvertedDocument(RegisteredDocument):
    """The registered text version plus the DOCX it was converted from (not stored)."""
    source: DocxSource


def _fail(code, message):
    raise AdapterFailure(code, message)


def docx_filename(value):
    """NFC original name; the registered text version is named ``<name>.txt``."""
    if not isinstance(value, str):
        _fail('DOCX_FILENAME_INVALID', 'A .docx file name is required.')
    value = unicodedata.normalize('NFC', value)
    if (not 6 <= len(value) <= 251 or value != value.strip() or not value.lower().endswith('.docx')
            or re.search(r'[/\\\x00-\x1f\x7f\ud800-\udfff]', value)):
        _fail('DOCX_FILENAME_INVALID', 'A .docx file name without path characters is required.')
    return value


def _member(archive, name):
    try:
        info = archive.getinfo(name)
    except KeyError:
        _fail('DOCX_INVALID', 'The Word document is missing its main part.')
    if info.flag_bits & 0x1:
        _fail('DOCX_ENCRYPTED', 'Encrypted Word documents are not supported.')
    if info.file_size > MAX_XML_BYTES:
        _fail('DOCX_TOO_LARGE', 'The Word document body exceeds the text conversion limit.')
    with archive.open(info) as stream:
        data = stream.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        _fail('DOCX_TOO_LARGE', 'The Word document body exceeds the text conversion limit.')
    return data


def _xml(data):
    from defusedxml import ElementTree
    return ElementTree.fromstring(data)


def _hidden(run):
    properties = run.find(W + 'rPr')
    flag = properties.find(W + 'vanish') if properties is not None else None
    return flag is not None and flag.get(W + 'val', 'true').lower() not in ('0', 'false', 'off')


def _check_depth(depth):
    if depth > MAX_DEPTH:
        _fail('DOCX_TOO_COMPLEX', 'The Word document nesting is too deep.')


def _lines(pieces):
    return [line.strip().lstrip('﻿').strip() for line in ''.join(pieces).split('\n')
            if line.strip().lstrip('﻿').strip()]


def _paragraph(paragraph, depth):
    """Lines of one paragraph, then any text-box paragraphs anchored in it."""
    pieces, anchored = [], []

    def walk(node, level):
        _check_depth(level)
        tag = node.tag
        if tag in SKIPPED:
            return
        if tag == W + 'r' and _hidden(node):
            return
        if tag == W + 'txbxContent':
            for _, lines in _items(node, level + 1):
                anchored.extend(lines)
            return
        if tag == MC + 'AlternateContent':
            choice = node.find(MC + 'Choice')
            if choice is not None:
                walk(choice, level + 1)
            return
        if tag == W + 't':
            pieces.append(node.text or '')
        elif tag == W + 'tab':
            pieces.append('\t')
        elif tag in (W + 'br', W + 'cr'):
            pieces.append('\n')
        elif tag == W + 'noBreakHyphen':
            pieces.append('-')
        for child in node:
            walk(child, level + 1)

    walk(paragraph, depth)
    return _lines(pieces) + anchored


def _cell(cell, depth):
    parts = []
    for _, lines in _items(cell, depth + 1):
        parts.extend(lines)
    return ' / '.join(parts)


def _children(node, tag, depth):
    """Direct children with this tag, looking through content-control wrappers."""
    _check_depth(depth)
    for child in node:
        if child.tag == tag:
            yield child
        elif child.tag in WRAPPERS:
            inner = child.find(WRAPPERS[child.tag]) if WRAPPERS[child.tag] else child
            if inner is not None:
                yield from _children(inner, tag, depth + 1)


def _table(table, depth):
    rows = []
    for row in _children(table, W + 'tr', depth + 1):
        cells = [_cell(cell, depth + 2) for cell in _children(row, W + 'tc', depth + 2)]
        if any(cells):
            rows.append(' | '.join(cells))
    return rows


def _items(container, depth):
    """('p'|'tbl', lines) in body order for a body, cell or text box."""
    _check_depth(depth)
    for child in container:
        if child.tag == W + 'p':
            yield 'p', _paragraph(child, depth + 1)
        elif child.tag == W + 'tbl':
            yield 'tbl', _table(child, depth + 1)
        elif child.tag in WRAPPERS:
            inner = child.find(WRAPPERS[child.tag]) if WRAPPERS[child.tag] else child
            if inner is not None:
                yield from _items(inner, depth + 1)
        elif child.tag == MC + 'AlternateContent':
            choice = child.find(MC + 'Choice')
            if choice is not None:
                yield from _items(choice, depth + 1)


def docx_requirement_text(raw):
    """Convert verified DOCX bytes; every failure is an AdapterFailure code."""
    if not isinstance(raw, bytes) or not raw.startswith(b'PK\x03\x04'):
        _fail('DOCX_INVALID', 'A Word .docx document is required.')
    if len(raw) > MAX_DOCX_BYTES:
        _fail('FILE_TOO_LARGE', 'The Word document exceeds the configured byte limit.')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if len(archive.infolist()) > MAX_ARCHIVE_MEMBERS:
                _fail('DOCX_TOO_COMPLEX', 'The Word document has too many parts.')
            types = _xml(_member(archive, '[Content_Types].xml'))
            main = [node.get('ContentType') for node in types.iter(CT + 'Override')
                    if node.get('PartName', '').lower() == '/word/document.xml']
            if main != [MAIN_CONTENT_TYPE]:
                _fail('DOCX_INVALID', 'Only Word .docx documents (not templates or macro documents) are supported.')
            root = _xml(_member(archive, 'word/document.xml'))
    except AdapterFailure:
        raise
    except Exception:
        _fail('DOCX_INVALID', 'The Word document could not be read.')
    body = root.find(W + 'body')
    if root.tag != W + 'document' or body is None:
        _fail('DOCX_INVALID', 'The Word document has no body.')
    blocks, paragraphs, rows = [], 0, 0
    try:
        for kind, lines in _items(body, 1):
            if not lines:
                continue
            if kind == 'p':
                paragraphs += 1
                if blocks and blocks[-1][0] == 'p':
                    blocks[-1][1].extend(lines)
                    continue
            else:
                rows += len(lines)
            blocks.append((kind, list(lines)))
    except RecursionError:
        _fail('DOCX_TOO_COMPLEX', 'The Word document nesting is too deep.')
    if not blocks:
        _fail('DOCX_TEXT_UNAVAILABLE', 'The Word document contains no body text.')
    text = '\n\n'.join('\n'.join(lines) for _, lines in blocks) + '\n'
    if len(text) > MAX_TEXT_CHARACTERS:
        _fail('DOCX_TEXT_TOO_LARGE', 'The converted Word text exceeds the requirement text limit.')
    try:
        inspect_text(text.encode('utf-8'))
    except AdapterFailure:
        _fail('DOCX_TEXT_UNAVAILABLE', 'The converted Word text contains unsupported characters.')
    return DocxText(text, len(blocks), paragraphs, rows)


def describe_source(raw, name, converted):
    return DocxSource(original_name=name, byte_size=len(raw), sha256=sha256(raw).hexdigest(),
                      blocks=converted.blocks, paragraphs=converted.paragraphs,
                      table_rows=converted.table_rows, characters=len(converted.text))
