"""Expose an identified, ruled PDF table row as one selectable source line."""
from collections import Counter
from dataclasses import dataclass
import re

from backend.agent_framework.a02_semantic import has_pending_addition, selected_range
from backend.agent_framework.a02_source_selection import SourceIndex, SourceLine
from backend.agent_framework.ports import AdapterFailure


ITEM_ID = re.compile(r"[A-Z]{1,3}[0-9]{1,3}")
CAPTION_HEADER = ("ID / 수준", "항목", "요구 내용", "판정 기준")
LITERAL_CAPTION_RULE = re.compile(
    r"자막 글자가 일치해야 함\."
    r"(?: 줄바꿈·공백·장식 효과는 허용하되 오탈자·문구 변경은 불가\.)?"
    r"(?: 참고 노출 시점 약 \d{1,3}:[0-5]\d\.)?"
)
# If the value reads like a condition or an instruction, leave it to the model.
CONDITIONAL_VALUE = re.compile(
    r"미확정|미정|추후|협의|대기|예정|확인\s*(?:후|중)|별도\s*(?:결정|확인)|"
    r"경우|때만|조건|예외|제외|대체|택일|예시|(?:하|되|있으|없으)면|해야|할\s*것|"
    r"필요\s*시|(?:하는|되는|있을|없을)\s*때|"
    r"\b(?:TBD|TBA|if|unless|except|pending)\b", re.IGNORECASE
)
MANUAL_CAPTION_RULES = {
    "자막 가독성": "사람이 가독성 검토. 정량 합격 점수로 사용하지 않음.",
    "표기 일관성": "표기 혼용 여부를 사람이 검토.",
}


@dataclass(frozen=True)
class CaptionRow:
    target: str
    label: str
    expected: str
    required: bool
    judgment: str


@dataclass(frozen=True)
class TableIndex(SourceIndex):
    # Original character offsets, per page. A selected range may not straddle a row.
    rows: dict[str, list[SourceLine]]
    row_ids: dict[str, str]
    caption_rows: dict[str, CaptionRow]


def select_video_range(selection, index):
    source, quote = selected_range(selection, index)
    if isinstance(index, TableIndex) and selection.start_line != selection.end_line:
        first, last = index.lines[selection.start_line], index.lines[selection.end_line]
        if any(first.start < row.end and row.start < last.end for row in index.rows.get(source.block_id, ())):
            raise AdapterFailure("A02_SOURCE_MISMATCH", "The selected range crosses a PDF table row.")
    return source, quote


def _four_columns(cells, hits, centers, raw):
    """Return four populated, ordered, nonoverlapping cells with exact source text."""
    columns = []
    indices = []
    for cell in cells:
        members = [i for i in hits if centers[i] in cell]
        if not members:
            continue  # MuPDF sometimes subdivides header padding into empty cells.
        if columns and columns[-1][0].x1 > cell.x0:
            return []
        columns.append((cell, "\n".join(raw[i] for i in members).strip()))
        indices.extend(members)
    return columns if len(columns) == 4 and indices == hits else []


def _caption_row(columns, header, item_id):
    if not columns or any(title.x0 < cell.x0 or title.x1 > cell.x1
                          for title, (cell, _) in zip(header, columns)):
        return None
    identity, label, expected, judgment = [value for _, value in columns]
    parts = identity.split()
    if len(parts) != 2 or parts[0] != item_id or parts[1] not in {"필수", "권장", "선택"}:
        return None
    if not re.fullmatch(r"[^:：\r\n]{1,40}", label) or not expected or len(expected) > 500:
        return None
    required = parts[1] == "필수"
    rule = " ".join(judgment.split())
    value = " ".join(expected.split())
    if has_pending_addition(value) or CONDITIONAL_VALUE.search(label + " " + value):
        return None
    if MANUAL_CAPTION_RULES.get(label) == rule:
        return CaptionRow("subtitle_quality", label, "", required, judgment)
    if label.endswith(("자막", "표기")) and LITERAL_CAPTION_RULE.fullmatch(rule):
        return CaptionRow("screen_text", label, expected, required, judgment)
    return None


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
        header = []
        for number, row in enumerate(table.rows):
            cells = [pymupdf.Rect(cell) for cell in row.cells if cell is not None]
            if len(cells) < 2:
                continue
            box = pymupdf.Rect(row.bbox)
            hits = [i for i, center in enumerate(centers) if center in box]
            if len(hits) < 2 or hits[-1] - hits[0] + 1 != len(hits):
                continue
            if number == 0:
                columns = _four_columns(cells, hits, centers, raw)
                if tuple(" ".join(value.split()) for _, value in columns) == CAPTION_HEADER:
                    header = [cell for cell, _ in columns]
            first, last = hits[0], hits[-1]
            item_id = raw[first].strip()
            if (not ITEM_ID.fullmatch(item_id) or centers[first] not in cells[0]
                    or any(centers[i] in cells[0] and ITEM_ID.fullmatch(raw[i].strip()) for i in hits[1:])
                    or not any(centers[i] in cell for cell in cells[1:] for i in hits[1:])):
                continue
            caption = _caption_row(_four_columns(cells, hits, centers, raw), header, item_id) if header else None
            found.append((first + 1, last + 1, item_id, caption))
    return found


def group_pdf_rows(data, index, payload):
    """Compact only the model view. Keep quotes and trace hashes in original coordinates."""
    import pymupdf
    lines = index.lines.copy()
    row_sources = {}
    row_ids = {}
    caption_rows = {}
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
            counts = Counter(n for a, b, _, _ in rows for n in range(a, b + 1))
            id_counts = Counter(item for _, _, item, _ in rows)
            consumed = set()
            replacements = {}
            for start, end, item_id, caption in rows:
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
                if caption is not None:
                    caption_rows[row_id] = caption
                replacements[row_id] = " ".join(source.text[row.start:row.end].split())
                consumed.update(f"{block_id}:L{n}" for n in range(start + 1, end + 1))
            for line_id in consumed:
                lines.pop(line_id, None)
            block["lines"] = [{**line, "text": replacements.get(line["id"], line["text"])}
                              for line in block["lines"] if line["id"] not in consumed]
    return TableIndex(lines, index.parts, row_sources, row_ids, caption_rows)
