"""Bound multipart bodies before Starlette spools them to disk."""
from app.core.config import settings
from starlette.responses import JSONResponse


class UploadBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or scope.get("method") != "POST" or not (
            path.endswith("/documents") or path.endswith("/images") or path.endswith("/upload-description")
        ):
            return await self.app(scope, receive, send)
        limit = (settings.IMAGE_MAX_BYTES if path.endswith("/images") else settings.max_document_bytes) + 1024 * 1024
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = limit + 1
        async def too_large():
            await JSONResponse({"detail": "Файл превышает допустимый размер"}, status_code=413)(scope, receive, send)
        if declared > limit:
            return await too_large()
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                return await too_large()
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        supplied = False
        async def bounded_receive():
            nonlocal supplied
            if not supplied:
                supplied = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()
        await self.app(scope, bounded_receive, send)
