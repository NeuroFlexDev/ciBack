from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from statistics import median_low
from zipfile import BadZipFile

import fitz
from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError


PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TXT_MIME = "text/plain"

_ATX_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s*(\S.*?)\s*#*\s*$")
_SETEXT_HEADING = re.compile(r"^\s*(=+|-+)\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-+*]\s+|\d+[.)]\s+)")
_DOCX_HEADING = re.compile(r"(?:heading|заголовок)\s*([1-9])", re.IGNORECASE)
_PAGE_NUMBER = re.compile(
    r"^(?:page\s+|стр(?:аница)?\.?\s*)?\d+$", re.IGNORECASE
)


@dataclass(frozen=True)
class ExtractedBlock:
    text: str
    page: int | None = None
    section: str | None = None
    block_type: str = "paragraph"
    heading_level: int | None = None


@dataclass(frozen=True)
class _PdfBlock:
    text: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    page_height: float
    font_size: float
    bold: bool


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    normalized = normalized.replace("\u00ad", "").replace("\u200b", "")
    normalized = normalized.replace("\u00a0", " ")
    return " ".join(normalized.split())


def _section_path(headings: dict[int, str], level: int, title: str) -> str:
    for previous_level in tuple(headings):
        if previous_level >= level:
            del headings[previous_level]
    headings[level] = title
    return " > ".join(headings[item] for item in sorted(headings))


def _iter_docx_blocks(document):
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def _docx_heading_level(paragraph: Paragraph) -> int | None:
    style = paragraph.style
    if style is not None:
        for candidate in (style.style_id, style.name):
            match = _DOCX_HEADING.search(str(candidate or ""))
            if match:
                return int(match.group(1))

    properties = paragraph._p.pPr
    outline = properties.find(qn("w:outlineLvl")) if properties is not None else None
    if outline is not None:
        raw_level = outline.get(qn("w:val"))
        if raw_level is not None and raw_level.isdigit():
            return min(9, int(raw_level) + 1)
    return None


def _docx_is_list_item(paragraph: Paragraph) -> bool:
    properties = paragraph._p.pPr
    if properties is not None and properties.numPr is not None:
        return True
    return bool(_LIST_ITEM.match(paragraph.text or ""))


def _docx_row_text(row) -> str:
    values: list[str] = []
    seen_cells: set[int] = set()
    for cell in row.cells:
        cell_identity = id(cell._tc)
        if cell_identity in seen_cells:
            continue
        seen_cells.add(cell_identity)
        text = _normalize_text(cell.text)
        if text:
            values.append(text)
    return " | ".join(values)


def _extract_docx(content: bytes) -> list[ExtractedBlock]:
    document = DocxDocument(BytesIO(content))
    blocks: list[ExtractedBlock] = []
    headings: dict[int, str] = {}
    section: str | None = None

    for block in _iter_docx_blocks(document):
        if isinstance(block, Paragraph):
            text = _normalize_text(block.text)
            if not text:
                continue
            heading_level = _docx_heading_level(block)
            if heading_level is not None:
                section = _section_path(headings, heading_level, text)
                blocks.append(
                    ExtractedBlock(
                        text=text,
                        section=section,
                        block_type="heading",
                        heading_level=heading_level,
                    )
                )
                continue
            blocks.append(
                ExtractedBlock(
                    text=text,
                    section=section,
                    block_type="list_item" if _docx_is_list_item(block) else "paragraph",
                )
            )
            continue

        for row in block.rows:
            text = _docx_row_text(row)
            if text:
                blocks.append(
                    ExtractedBlock(
                        text=text,
                        section=section,
                        block_type="table_row",
                    )
                )
    return blocks


def _pdf_blocks(document) -> list[_PdfBlock]:
    blocks: list[_PdfBlock] = []
    for page_number, page in enumerate(document, start=1):
        payload = page.get_text("dict", sort=True)
        page_height = float(page.rect.height)
        for raw_block in payload.get("blocks", []):
            if raw_block.get("type", 0) != 0:
                continue
            line_texts: list[str] = []
            font_sizes: list[float] = []
            bold = False
            for line in raw_block.get("lines", []):
                span_texts: list[str] = []
                for span in line.get("spans", []):
                    text = _normalize_text(str(span.get("text") or ""))
                    if not text:
                        continue
                    span_texts.append(text)
                    font_sizes.append(float(span.get("size") or 0))
                    font_name = str(span.get("font") or "").casefold()
                    flags = int(span.get("flags") or 0)
                    bold = bold or "bold" in font_name or bool(flags & 16)
                line_text = _normalize_text(" ".join(span_texts))
                if line_text:
                    line_texts.append(line_text)
            text = _normalize_text(" ".join(line_texts))
            if not text:
                continue
            bbox = raw_block.get("bbox", (0, 0, 0, 0))
            x0, y0, x1, y1 = (float(value) for value in bbox)
            blocks.append(
                _PdfBlock(
                    text=text,
                    page=page_number,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    page_height=page_height,
                    font_size=max(font_sizes, default=0),
                    bold=bold,
                )
            )
    return sorted(blocks, key=lambda item: (item.page, round(item.y0, 1), item.x0))


def _pdf_margin_key(block: _PdfBlock) -> str | None:
    is_top = block.y1 <= block.page_height * 0.12
    is_bottom = block.y0 >= block.page_height * 0.88
    if not (is_top or is_bottom) or len(block.text) > 200:
        return None
    return re.sub(r"\d+", "#", block.text.casefold())


def _pdf_repeated_margins(blocks: list[_PdfBlock], page_count: int) -> set[str]:
    if page_count < 2:
        return set()
    pages_by_text: dict[str, set[int]] = {}
    for block in blocks:
        key = _pdf_margin_key(block)
        if key is not None:
            pages_by_text.setdefault(key, set()).add(block.page)
    threshold = max(2, math.ceil(page_count * 0.5))
    return {
        text for text, pages in pages_by_text.items() if len(pages) >= threshold
    }


def _is_pdf_heading(block: _PdfBlock, body_font_size: float) -> bool:
    if len(block.text) > 240 or len(block.text.split()) > 24:
        return False
    if block.font_size >= body_font_size * 1.2:
        return True
    sentence_like = block.text.endswith((".", "!", "?", ";", ":"))
    return block.bold and block.font_size >= body_font_size and not sentence_like


def _extract_pdf(content: bytes) -> list[ExtractedBlock]:
    with fitz.open(stream=content, filetype="pdf") as document:
        if document.needs_pass:
            raise ValueError("Encrypted PDF is not supported")
        raw_blocks = _pdf_blocks(document)
        repeated_margins = _pdf_repeated_margins(raw_blocks, document.page_count)

    filtered = [
        block
        for block in raw_blocks
        if _pdf_margin_key(block) not in repeated_margins
        and not (
            _pdf_margin_key(block) is not None
            and _PAGE_NUMBER.fullmatch(block.text) is not None
        )
    ]
    if not filtered:
        return []

    positive_sizes = [block.font_size for block in filtered if block.font_size > 0]
    body_font_size = median_low(positive_sizes) if positive_sizes else 1.0
    heading_sizes = sorted(
        {
            round(block.font_size, 1)
            for block in filtered
            if _is_pdf_heading(block, body_font_size)
            and block.font_size > body_font_size
        },
        reverse=True,
    )
    headings: dict[int, str] = {}
    section: str | None = None
    result: list[ExtractedBlock] = []
    for block in filtered:
        if _is_pdf_heading(block, body_font_size):
            rounded_size = round(block.font_size, 1)
            if rounded_size in heading_sizes:
                heading_level = heading_sizes.index(rounded_size) + 1
            else:
                heading_level = min(6, len(heading_sizes) + 1)
            section = _section_path(headings, heading_level, block.text)
            block_type = "heading"
        else:
            heading_level = None
            block_type = "paragraph"
        result.append(
            ExtractedBlock(
                text=block.text,
                page=block.page,
                section=section,
                block_type=block_type,
                heading_level=heading_level,
            )
        )
    return result


def _extract_txt(content: bytes) -> list[ExtractedBlock]:
    lines = content.decode("utf-8-sig").splitlines()
    blocks: list[ExtractedBlock] = []
    headings: dict[int, str] = {}
    section: str | None = None
    paragraph: list[str] = []
    paragraph_section: str | None = None
    in_fence = False
    fence_marker: str | None = None

    def flush_paragraph() -> None:
        nonlocal paragraph, paragraph_section
        text = _normalize_text(" ".join(paragraph))
        if text:
            blocks.append(
                ExtractedBlock(
                    text=text,
                    section=paragraph_section,
                    block_type="list_item" if _LIST_ITEM.match(text) else "paragraph",
                )
            )
        paragraph = []
        paragraph_section = None

    index = 0
    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()

        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = None

        if not in_fence:
            atx = _ATX_HEADING.match(raw_line)
            if atx:
                flush_paragraph()
                title = _normalize_text(atx.group(2))
                level = len(atx.group(1))
                section = _section_path(headings, level, title)
                blocks.append(
                    ExtractedBlock(
                        text=title,
                        section=section,
                        block_type="heading",
                        heading_level=level,
                    )
                )
                index += 1
                continue

            if paragraph and _SETEXT_HEADING.fullmatch(stripped):
                title = _normalize_text(" ".join(paragraph))
                paragraph = []
                paragraph_section = None
                level = 1 if stripped.startswith("=") else 2
                section = _section_path(headings, level, title)
                blocks.append(
                    ExtractedBlock(
                        text=title,
                        section=section,
                        block_type="heading",
                        heading_level=level,
                    )
                )
                index += 1
                continue

        if not stripped:
            flush_paragraph()
        else:
            if not paragraph:
                paragraph_section = section
            paragraph.append(raw_line)
        index += 1

    flush_paragraph()
    return blocks


def extract_blocks(content: bytes, mime_type: str) -> list[ExtractedBlock]:
    if mime_type == PDF_MIME:
        try:
            return _extract_pdf(content)
        except ValueError:
            raise
        except (fitz.FileDataError, RuntimeError) as exc:
            raise ValueError("Invalid PDF document") from exc
    if mime_type == DOCX_MIME:
        try:
            return _extract_docx(content)
        except ValueError:
            raise
        except (
            AttributeError,
            BadZipFile,
            KeyError,
            OSError,
            PackageNotFoundError,
            XMLSyntaxError,
        ) as exc:
            raise ValueError("Invalid DOCX document") from exc
    if mime_type == TXT_MIME:
        try:
            return _extract_txt(content)
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid UTF-8 text document") from exc
    raise ValueError("Unsupported document type")


def _semantic_end(text: str, start: int, max_chars: int) -> int:
    hard_end = min(len(text), start + max_chars)
    if hard_end == len(text):
        return hard_end

    minimum = start + max(1, int(max_chars * 0.55))
    window = text[minimum:hard_end]
    newline = window.rfind("\n")
    if newline >= 0:
        return minimum + newline

    sentence_ends = list(re.finditer(r"[.!?…](?:[\"'»)\]]*)\s+", window))
    if sentence_ends:
        match = sentence_ends[-1]
        return minimum + match.end() - len(match.group(0)) + len(
            match.group(0).rstrip()
        )

    whitespace = max(window.rfind(" "), window.rfind("\t"))
    if whitespace >= 0:
        return minimum + whitespace
    return hard_end


def _next_window_start(
    text: str, start: int, end: int, overlap_chars: int
) -> int:
    candidate = max(start + 1, end - overlap_chars)
    if candidate >= len(text):
        return len(text)
    while candidate < end and candidate > 0 and not text[candidate - 1].isspace():
        candidate += 1
    while candidate < len(text) and text[candidate].isspace():
        candidate += 1
    return candidate if candidate > start else end


def _text_windows(text: str, *, max_chars: int, overlap_chars: int):
    start = 0
    while start < len(text):
        end = _semantic_end(text, start, max_chars)
        if end <= start:
            end = min(len(text), start + max_chars)
        clean = text[start:end].strip()
        if clean:
            yield clean
        if end >= len(text):
            break
        start = _next_window_start(text, start, end, overlap_chars)


def chunk_blocks(
    blocks: list[ExtractedBlock], *, max_chars: int, overlap_chars: int
) -> list[dict]:
    if max_chars <= 0:
        raise ValueError("Chunk size must be positive")
    if overlap_chars < 0:
        raise ValueError("Chunk overlap must not be negative")
    if overlap_chars >= max_chars:
        raise ValueError("Chunk overlap must be smaller than chunk size")

    chunks: list[dict] = []
    group: list[str] = []
    group_page: int | None = None
    group_section: str | None = None

    def emit_group() -> None:
        nonlocal group
        text = "\n".join(group).strip()
        for window in _text_windows(
            text, max_chars=max_chars, overlap_chars=overlap_chars
        ):
            chunks.append(
                {
                    "text": window,
                    "page": group_page,
                    "section": group_section,
                    "metadata_json": {
                        "page": group_page,
                        "section": group_section,
                    },
                    "chunk_index": len(chunks),
                }
            )
        group = []

    for block in blocks:
        text = _normalize_text(block.text)
        if not text:
            continue
        metadata_changed = bool(group) and (
            block.page != group_page or block.section != group_section
        )
        if metadata_changed:
            emit_group()
        if not group:
            group_page = block.page
            group_section = block.section
        group.append(text)

    emit_group()
    return chunks
