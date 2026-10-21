import hashlib
import logging
import uuid
from fastapi.responses import JSONResponse
from redis import Redis, RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.config import settings

logger = logging.getLogger(__name__)
RATE_SCRIPT = "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n"


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        limited = request.url.path.startswith(('/api/auth/', '/api/lti/')) or request.url.path in ('/api/legal/requests', '/api/billing/webhooks/yookassa')
        state_creation = request.method == "GET" and (request.url.path.endswith('/start') or request.url.path.endswith('/login')) and limited
        if settings.ENV != "test" and settings.AUTH_RATE_LIMIT_ENABLED and (request.method == "POST" or state_creation) and limited:
            # Use the ASGI peer address. Forwarded headers are accepted only from trusted proxies by uvicorn.
            peer = request.client.host if request.client else 'unknown'
            key = 'lernium:auth-rate:' + hashlib.sha256(peer.encode()).hexdigest()
            client = None
            try:
                from starlette.concurrency import run_in_threadpool
                client = Redis.from_url(settings.REDIS_URL, socket_timeout=1, socket_connect_timeout=1)
                count = await run_in_threadpool(client.eval, RATE_SCRIPT, 1, key)
                if count > settings.AUTH_RATE_LIMIT_PER_MINUTE:
                    return JSONResponse({"detail": "Слишком много попыток. Повторите через минуту.", "request_id": request_id}, status_code=429, headers={"Retry-After": "60", "X-Request-ID": request_id})
            except RedisError:
                return JSONResponse({"detail": "Отправка временно недоступна. Повторите позже.", "request_id": request_id}, status_code=503, headers={"X-Request-ID": request_id})
            finally:
                if client:
                    client.close()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled request error id=%s method=%s path=%s", request_id, request.method, request.url.path)
            response = JSONResponse({"detail": "Не удалось выполнить запрос. Попробуйте позже.", "request_id": request_id}, status_code=500)
        response.headers['X-Request-ID'] = request_id
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response
