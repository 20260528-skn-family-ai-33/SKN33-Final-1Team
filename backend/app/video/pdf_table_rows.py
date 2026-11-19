"""Expose an identified, ruled PDF table row as one selectable source line."""
from collections import Counter
from dataclasses import dataclass
import re

from backend.agent_framework.a02_semantic import selected_range
from backend.agent_framework.a02_source_selection import SourceIndex, SourceLine
from backend.agent_framework.ports import AdapterFailure


ITEM_ID = re.compile(r"[A-Z]{1,3}[0-9]{1,3}")


@dataclass(frozen=True)
class TableIndex(SourceIndex):
    # Original character offsets, per page. A selected range may not straddle a row.
    rows: dict[str, list[SourceLine]]
    row_ids: dict[str, str]


def select_video_range(selection, index):
    source, quote = selected_range(selection, index)
    if isinstance(index, TableIndex) and selection.start_line != selection.end_line:
        first, last = index.lines[selection.start_line], index.lines[selection.end_line]
        if any(first.start < row.end and row.start < last.end for row in index.rows.get(source.block_id, ())):
            raise AdapterFailure("A02_SOURCE_MISMATCH", "The selected range crosses a PDF table row.")
    return source, quote


def _page_rows(page, source):
    """Return inclusive line numbers only for unambiguously aligned, contiguous rows."""
    import pymupdf
    raw = source.text.splitlines()
    if page.rotation or not any(ITEM_ID.fullmatch(line.strip()) for line in raw):
        return []
    # Bound the grid intersection search on complex illustrations. Never decode
    # embedded bitmaps to obtain line positions. Plain source lines remain usable.
    paths = page.get_drawings()
    if not paths or len(paths) > 512 or sum(len(p["items"]) for p in paths) > 1024:
        return []
    blocks = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]
    lines = [line for block in blocks if block["type"] == 0 for line in block["lines"]]
    if raw != ["".join(span["text"] for span in line["spans"]) for line in lines]:
        return []
    centers = []
    for line in lines:
        x0, y0, x1, y1 = line["bbox"]
        centers.append(pymupdf.Point((x0 + x1) / 2, (y0 + y1) / 2))
    options = {"use_layout": False} if pymupdf.pymupdf_version_tuple >= (1, 28, 0) else {}
    tables = page.find_tables(paths=paths, **options).tables
    found = []
    for table in tables:
        for row in table.rows:
            cells = [pymupdf.Rect(cell) for cell in row.cells if cell is not None]
            if len(cells) < 2:
                continue
            box = pymupdf.Rect(row.bbox)
            hits = [i for i, center in enumerate(centers) if center in box]
            if len(hits) < 2 or hits[-1] - hits[0] + 1 != len(hits):
                continue
            first, last = hits[0], hits[-1]
            item_id = raw[first].strip()
            if (not ITEM_ID.fullmatch(item_id) or centers[first] not in cells[0]
                    or any(centers[i] in cells[0] and ITEM_ID.fullmatch(raw[i].strip()) for i in hits[1:])
                    or not any(centers[i] in cell for cell in cells[1:] for i in hits[1:])):
                continue
            found.append((first + 1, last + 1, item_id))
    return found


def group_pdf_rows(data, index, payload):
    """Compact only the model view. Keep quotes and trace hashes in original coordinates."""
    import pymupdf
    lines = index.lines.copy()
    row_sources = {}
    row_ids = {}
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        for block in payload:
            block_id = block["block_id"]
            first_id = block["lines"][0]["id"]
            source = lines[first_id].source
            try:
                rows = _page_rows(pdf[source.page - 1], source)
            except (RuntimeError, ValueError, IndexError, TypeError):
                # Optional geometry can fail on PDFs whose ordinary text is valid.
                continue
            # Overlapping detections or repeated IDs cannot establish a unique row.
            counts = Counter(n for a, b, _ in rows for n in range(a, b + 1))
            id_counts = Counter(item for _, _, item in rows)
            consumed = set()
            replacements = {}
            for start, end, item_id in rows:
                if id_counts[item_id] != 1 or any(counts[n] != 1 for n in range(start, end + 1)):
                    continue
                row_id = f"{block_id}:L{start}"
                last_id = f"{block_id}:L{end}"
                if row_id not in lines or last_id not in lines:
                    continue
                row = SourceLine(source, lines[row_id].start, lines[last_id].end)
                if row.end - row.start > 4000:  # selected_range limit
                    continue
                lines[row_id] = row
                row_sources.setdefault(block_id, []).append(row)
                row_ids[row_id] = item_id
                replacements[row_id] = " ".join(source.text[row.start:row.end].split())
                consumed.update(f"{block_id}:L{n}" for n in range(start + 1, end + 1))
            for line_id in consumed:
                lines.pop(line_id, None)
            block["lines"] = [{**line, "text": replacements.get(line["id"], line["text"])}
                              for line in block["lines"] if line["id"] not in consumed]
    return TableIndex(lines, index.parts, row_sources, row_ids)
