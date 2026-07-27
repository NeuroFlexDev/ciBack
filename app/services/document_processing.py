from dataclasses import dataclass
from io import BytesIO

import fitz
from docx import Document as DocxDocument


@dataclass(frozen=True)
class ExtractedBlock:
    text: str
    page: int | None = None
    section: str | None = None


def extract_blocks(content: bytes, mime_type: str) -> list[ExtractedBlock]:
    if mime_type == "application/pdf":
        blocks = []
        with fitz.open(stream=content, filetype="pdf") as document:
            for page_number, page in enumerate(document, start=1):
                for raw in page.get_text("blocks"):
                    text = " ".join(str(raw[4]).split())
                    if text:
                        blocks.append(ExtractedBlock(text=text, page=page_number))
        return blocks

    if mime_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        blocks = []
        section = None
        document = DocxDocument(BytesIO(content))
        for paragraph in document.paragraphs:
            text = " ".join(paragraph.text.split())
            if not text:
                continue
            if paragraph.style and paragraph.style.name.lower().startswith("heading"):
                section = text
            blocks.append(ExtractedBlock(text=text, section=section))
        return blocks

    if mime_type == "text/plain":
        blocks = []
        section = None
        for raw_line in content.decode("utf-8-sig").splitlines():
            text = " ".join(raw_line.split())
            if not text:
                continue
            if text.startswith("#"):
                section = text.lstrip("#").strip() or section
            blocks.append(ExtractedBlock(text=text, section=section))
        return blocks

    raise ValueError("Unsupported document type")


def chunk_blocks(
    blocks: list[ExtractedBlock], *, max_chars: int, overlap_chars: int
) -> list[dict]:
    if overlap_chars >= max_chars:
        raise ValueError("Chunk overlap must be smaller than chunk size")

    chunks: list[dict] = []
    current_text = ""
    current_page = None
    current_section = None

    def emit(text: str, page: int | None, section: str | None) -> None:
        clean = text.strip()
        if clean:
            chunks.append(
                {
                    "text": clean,
                    "page": page,
                    "section": section,
                    "metadata_json": {"page": page, "section": section},
                    "chunk_index": len(chunks),
                }
            )

    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        metadata_changed = (
            current_text
            and (block.page != current_page or block.section != current_section)
        )
        if metadata_changed:
            emit(current_text, current_page, current_section)
            current_text = ""

        if not current_text:
            current_page, current_section = block.page, block.section
        candidate = f"{current_text}\n{text}".strip()
        if len(candidate) <= max_chars:
            current_text = candidate
            continue

        if current_text:
            emit(current_text, current_page, current_section)
            prefix = current_text[-overlap_chars:] if overlap_chars else ""
            current_text = f"{prefix}\n{text}".strip()
        else:
            current_text = text

        while len(current_text) > max_chars:
            emit(current_text[:max_chars], current_page, current_section)
            start = max_chars - overlap_chars
            current_text = current_text[start:].strip()

    emit(current_text, current_page, current_section)
    return chunks
