"""Bounded-memory streaming for immutable learner downloads."""
from hashlib import sha256
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from app.services.file_storage import get_file_storage


def file_response(request: Request, key: str, size: int, digest: str, mime: str):
    storage = get_file_storage()
    path = storage._target(key)
    hasher = sha256()
    try:
        with path.open('rb') as source:
            while chunk := source.read(1024 * 1024):
                hasher.update(chunk)
    except (OSError, ValueError):
        raise HTTPException(503, 'Файл временно недоступен') from None
    if path.stat().st_size != size or hasher.hexdigest() != digest:
        raise HTTPException(503, 'Проверка целостности файла не пройдена')

    etag = f'"{digest}"'
    range_header = request.headers.get('range')
    # A stale If-Range means restart from the full immutable file.
    if request.headers.get('if-range') and request.headers['if-range'] != etag:
        range_header = None
    start, end, status = 0, max(0, size - 1), 200
    if range_header:
        try:
            unit, interval = range_header.split('=', 1)
            if unit != 'bytes' or ',' in interval:
                raise ValueError
            left, right = interval.split('-', 1)
            if left:
                start = int(left)
                end = int(right) if right else size - 1
            else:
                suffix = int(right)
                if suffix <= 0:
                    raise ValueError
                start = max(0, size - suffix)
            if start >= size or end < start or end >= size:
                raise ValueError
            status = 206
        except ValueError:
            raise HTTPException(416, 'Invalid byte range', headers={
                'Accept-Ranges': 'bytes', 'ETag': etag, 'Content-Range': f'bytes */{size}'}) from None
    length = max(0, end - start + 1)
    def chunks():
        with path.open('rb') as source:
            source.seek(start)
            remaining = length
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    headers = {'Accept-Ranges': 'bytes', 'ETag': etag, 'Content-Length': str(length),
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'}
    if status == 206:
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'
    return StreamingResponse(chunks(), status_code=status, media_type=mime, headers=headers)
