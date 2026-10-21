"""Bounded document parsing shared by new and legacy upload paths."""
from __future__ import annotations

import multiprocessing
import re
from io import BytesIO
from zipfile import ZipFile, BadZipFile

from app.core.config import settings


def validate_container(content: bytes, mime_type: str) -> None:
    if not content or len(content) > settings.max_document_bytes:
        raise ValueError("Документ пуст или превышает допустимый размер")
    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            with ZipFile(BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > settings.DOCUMENT_MAX_ARCHIVE_ENTRIES:
                    raise ValueError("Слишком много объектов в DOCX")
                total = sum(item.file_size for item in entries)
                if total > settings.DOCUMENT_MAX_UNPACKED_BYTES:
                    raise ValueError("Распакованный DOCX превышает лимит")
                for entry in entries:
                    if entry.flag_bits & 1 or entry.file_size > settings.DOCUMENT_MAX_UNPACKED_BYTES:
                        raise ValueError("Зашифрованный или слишком большой объект DOCX")
                    if entry.file_size > max(1024 * 1024, entry.compress_size * 200):
                        raise ValueError("Недопустимая степень сжатия DOCX")
                    if entry.filename.endswith(".xml"):
                        payload = archive.read(entry)
                        if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", payload, re.I):
                            raise ValueError("DOCX содержит недопустимые XML-сущности")
                    if entry.filename.endswith(".rels"):
                        data = archive.read(entry)
                        if re.search(rb'TargetMode\s*=\s*[\"\x27]External', data, re.I):
                            # Normal hyperlinks are data; external images/templates/OLE
                            # must never be fetched by any parser or subsequent renderer.
                            if re.search(rb'Type\s*=\s*[\"\x27][^\"\x27]*(?:image|oleObject|attachedTemplate)', data, re.I):
                                raise ValueError("DOCX содержит внешние вложения")
        except BadZipFile as exc:
            raise ValueError("Повреждённый DOCX") from exc


def _extract_child(connection, content: bytes, mime_type: str):
    try:
        from app.services.document_processing import extract_blocks, extraction_usage
        usage = {"ocr_pages": 0}
        extraction_usage.set(usage)
        blocks = extract_blocks(content, mime_type)
        if sum(len(block.text) for block in blocks) > settings.DOCUMENT_MAX_EXTRACTED_CHARS:
            raise ValueError("Извлечённый текст превышает лимит")
        connection.send((True, (blocks, usage)))
    except Exception as exc:
        connection.send((False, str(exc) if isinstance(exc, ValueError) else "Не удалось обработать документ"))
    finally:
        connection.close()


def bounded_extract_blocks(content: bytes, mime_type: str, *, with_usage=False):
    validate_container(content, mime_type)
    # In production native parsers/OCR run in an expendable process. A thread
    # timeout cannot stop a decompression bomb or a blocked OCR engine.
    if settings.ENV == "test":
        from app.services.document_processing import extract_blocks, extraction_usage
        usage = {"ocr_pages": 0}
        token = extraction_usage.set(usage)
        try:
            blocks = extract_blocks(content, mime_type)
            return (blocks, usage) if with_usage else blocks
        finally:
            extraction_usage.reset(token)
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_extract_child, args=(sender, content, mime_type), daemon=True)
    process.start()
    sender.close()
    try:
        if not receiver.poll(settings.DOCUMENT_PROCESSING_TIMEOUT_SECONDS):
            raise ValueError("Истекло время обработки документа; разделите его на части")
        ok, result = receiver.recv()
        if not ok:
            raise ValueError(result)
        return result if with_usage else result[0]
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
