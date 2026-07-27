from io import BytesIO

import fitz
from docx import Document as DocxDocument

from app.services.document_processing import chunk_blocks, extract_blocks


def test_txt_extraction_and_chunking_preserve_section():
    blocks = extract_blocks(
        "# Введение\nПервый абзац.\nВторой абзац.".encode(), "text/plain"
    )
    chunks = chunk_blocks(blocks, max_chars=40, overlap_chars=5)

    assert blocks[0].section == "Введение"
    assert chunks
    assert all(chunk["section"] == "Введение" for chunk in chunks)
    assert [chunk["chunk_index"] for chunk in chunks] == list(range(len(chunks)))


def test_docx_extraction_preserves_heading():
    stream = BytesIO()
    document = DocxDocument()
    document.add_heading("Раздел", level=1)
    document.add_paragraph("Содержимое раздела")
    document.save(stream)

    blocks = extract_blocks(
        stream.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert blocks[-1].text == "Содержимое раздела"
    assert blocks[-1].section == "Раздел"


def test_pdf_extraction_preserves_page_number():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "PDF content")
    content = document.tobytes()
    document.close()

    blocks = extract_blocks(content, "application/pdf")

    assert blocks[0].page == 1
    assert "PDF content" in blocks[0].text
