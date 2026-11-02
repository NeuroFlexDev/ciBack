"""Bound multipart bodies before Starlette spools them to disk."""
from app.core.config import settings
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from tempfile import SpooledTemporaryFile


class UploadBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or scope.get("method") != "POST" or not (
            path.endswith("/documents") or path.endswith("/images") or path.endswith("/media") or path.endswith("/upload-description")
        ):
            return await self.app(scope, receive, send)
        limit = (settings.VIDEO_MAX_BYTES if path.endswith("/media") else settings.IMAGE_MAX_BYTES if path.endswith("/images") else settings.max_document_bytes) + 1024 * 1024
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = limit + 1
        async def too_large():
            await JSONResponse({"detail": "Файл превышает допустимый размер"}, status_code=413)(scope, receive, send)
        if declared > limit:
            return await too_large()
        # Spool large requests instead of retaining and joining the full video
        # in RAM. Replay bounded chunks to Starlette's multipart parser.
        with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as body:
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > limit:
                    return await too_large()
                await run_in_threadpool(body.write, chunk)
                if not message.get("more_body", False):
                    break
            await run_in_threadpool(body.seek, 0)
            remaining = size
            supplied = False
            async def bounded_receive():
                nonlocal remaining, supplied
                if remaining or not supplied:
                    supplied = True
                    chunk = await run_in_threadpool(body.read, 1024 * 1024)
                    remaining -= len(chunk)
                    return {"type": "http.request", "body": chunk, "more_body": remaining > 0}
                return await receive()
            await self.app(scope, bounded_receive, send)
