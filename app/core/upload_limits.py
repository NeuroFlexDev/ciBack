"""Bound multipart bodies before Starlette spools them to disk."""

from tempfile import SpooledTemporaryFile

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.core.config import settings


class _BoundedUploadReceive:
    """Replay the spooled body once, then delegate disconnect events."""

    def __init__(self, body, receive, size):
        self.body = body
        self.receive = receive
        self.remaining = size
        self.supplied = False

    async def __call__(self):
        if self.remaining or not self.supplied:
            self.supplied = True
            chunk = await run_in_threadpool(self.body.read, 1024 * 1024)
            self.remaining -= len(chunk)
            return {"type": "http.request", "body": chunk, "more_body": self.remaining > 0}
        return await self.receive()


class UploadBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not (
                path.endswith("/documents")
                or path.endswith("/images")
                or path.endswith("/media")
                or path.endswith("/upload-description")
            )
        ):
            return await self.app(scope, receive, send)
        limit = (
            settings.VIDEO_MAX_BYTES
            if path.endswith("/media")
            else settings.IMAGE_MAX_BYTES
            if path.endswith("/images")
            else settings.max_document_bytes
        ) + 1024 * 1024
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = limit + 1

        async def too_large():
            await JSONResponse({"detail": "Файл превышает допустимый размер"}, status_code=413)(
                scope, receive, send
            )

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
            await self.app(scope, _BoundedUploadReceive(body, receive, size), send)
