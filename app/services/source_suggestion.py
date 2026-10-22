"""Editable, extractive course metadata: source text never becomes an instruction."""
import re
from pathlib import PurePath


def _plain(value: str) -> str:
    value = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"<[^>]*>", "", value)
    return re.sub(r"\s+", " ", value).strip(" #*_\t\r\n")


def _clip(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    return value[:maximum - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def source_suggestion(documents) -> dict:
    titles, summaries, sources = [], [], []
    for document in documents:
        # The first few source chunks are sufficient; no provider request or hidden write.
        chunks = sorted((c for c in document.chunks if not c.is_deleted and c.document_version == document.version),
                        key=lambda c: c.chunk_index)[:8]
        heading = next((_plain(c.section.split(" > ")[0]) for c in chunks if c.section), "")
        text = "\n".join(c.text for c in chunks)[:16000]
        lines = [_plain(line) for line in text.splitlines()]
        first_line = next((line for line in lines if 4 <= len(line) <= 180 and not re.fullmatch(r"[\d\W]+", line)), "")
        title = heading or first_line or _plain(PurePath(document.original_filename).stem.replace("_", " "))
        title = _clip(title, 160)
        if title and title.casefold() not in {item.casefold() for item in titles}:
            titles.append(title)
        candidates = []
        for line in lines:
            if len(line) < 35 or line.casefold() == title.casefold() or re.match(r"^(?:содержание|оглавление|contents|copyright|©)\b", line, re.I):
                continue
            for sentence in re.split(r"(?<=[.!?])\s+(?=[А-ЯA-Z])", line):
                if len(sentence) >= 35 and sentence not in candidates:
                    candidates.append(sentence)
        summary = _clip(" ".join(candidates[:3]), 600)
        if summary and summary not in summaries:
            summaries.append(summary)
        sources.append({'id':document.id, 'version':document.version, 'filename':document.original_filename})
    return {'title':_clip(" · ".join(titles), 200), 'goal':_clip("\n\n".join(summaries), 1800),
            'sources':sources, 'method':'source_excerpt'}
