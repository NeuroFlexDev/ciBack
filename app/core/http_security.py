import hashlib
import hmac
import logging
import re
import uuid
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from redis import Redis, RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.config import settings

logger = logging.getLogger(__name__)
RATE_SCRIPT = "local r={}; for i,k in ipairs(KEYS) do local n=redis.call('INCR',k); if n==1 then redis.call('EXPIRE',k,60) end; r[i]=n; end; return r"
MAX_AUTH_JSON_BYTES = 16 * 1024
EMAIL_PATHS = {'/api/auth/login', '/api/auth/register', '/api/auth/password-reset', '/api/legal/requests'}
TOKEN_FIELDS = {
    '/api/auth/password-reset/confirm': 'token',
    '/api/auth/invitations/inspect': 'token',
    '/api/auth/invitations/accept': 'token',
    '/api/auth/mfa/verify': 'challenge_token',
    '/api/auth/oidc/exchange': 'state',
    '/api/lti/exchange': 'state',
}


def private_key(scope, value):
    # An email, bearer or one-time credential is never stored in Redis or logs.
    return 'lernium:auth-rate:' + scope + ':' + hmac.new(
        settings.JWT_SECRET.get_secret_value().encode(), value.encode('utf-8', errors='surrogatepass'), hashlib.sha256).hexdigest()


def json_media_type(request):
    # Match FastAPI's JSON-body selection, including vendor +json and an
    # omitted Content-Type. Media types are case insensitive.
    raw = request.headers.get('content-type')
    if raw is None:
        return True
    media = raw.partition(';')[0].strip().lower()
    return media == 'application/json' or (media.startswith('application/') and media.endswith('+json'))


async def bounded_json(request, limit):
    declared = request.headers.get('content-length')
    if declared is not None:
        try:
            length = int(declared)
        except ValueError:
            raise HTTPException(400, 'Некорректная длина запроса') from None
        if length < 0:
            raise HTTPException(400, 'Некорректная длина запроса')
        if length > limit:
            raise HTTPException(413, 'Запрос превышает допустимый размер')
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, 'Запрос превышает допустимый размер')
        body.extend(chunk)
    # BaseHTTPMiddleware's cached Request replays _body to the downstream ASGI
    # app. Reading only stream() without this cache would consume the body.
    request._body = bytes(body)
    try:
        return await request.json()
    except RecursionError:
        raise HTTPException(400, 'Слишком сложная структура JSON') from None
    except (ValueError, UnicodeDecodeError):
        return None


def authenticated_bucket(request):
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 4096:
        return None
    from app.core.security import decode_access_token, TokenValidationError
    try:
        claims = decode_access_token(token)
        # Valid sessions of one account share a budget even if tokens rotate.
        return private_key('principal', claims['sub'])
    except TokenValidationError:
        return private_key('session', token)


async def account_bucket(request):
    path = request.url.path.rstrip('/')
    # LTI launch uses form_post with an id_token up to 32 KiB. Do not consume
    # its form stream or apply the smaller JSON credential-body limit.
    form_launch = bool(re.fullmatch(r'/api/lti/[^/]+/launch', path))
    credential_json = path.startswith('/api/auth/') or path in {'/api/change-password', '/api/me/email', '/api/me/email/confirm', '/api/lti/exchange', '/api/legal/requests'}
    if request.method == 'POST' and credential_json and not form_launch and json_media_type(request):
        body = await bounded_json(request, 32 * 1024 if path == '/api/legal/requests' else MAX_AUTH_JSON_BYTES)
        if isinstance(body, dict):
            if path in EMAIL_PATHS:
                email = body.get('email')
                if isinstance(email, str) and 0 < len(email) <= 320:
                    return private_key('account', email.strip().lower())
            field = TOKEN_FIELDS.get(path)
            if field:
                value = body.get(field)
                if isinstance(value, str) and 0 < len(value) <= 2048:
                    return private_key(path + ':' + field, value)
    # Authenticated mutations use their verified principal, never an unrelated
    # extra email/token field that a downstream schema might ignore.
    return authenticated_bucket(request)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        shared_auth = request.url.path.startswith(('/api/auth/', '/api/lti/')) or request.url.path in {'/api/change-password', '/api/me/email', '/api/me/email/confirm'}
        limited = shared_auth or request.url.path in ('/api/legal/requests', '/api/billing/webhooks/yookassa')
        state_creation = request.method == "GET" and (request.url.path.endswith('/start') or request.url.path.endswith('/login')) and limited
        if settings.ENV != "test" and settings.AUTH_RATE_LIMIT_ENABLED and (request.method == "POST" or state_creation) and limited:
            # Use the ASGI peer address. Forwarded headers are accepted only from trusted proxies by uvicorn.
            peer = request.client.host if request.client else 'unknown'
            # A classroom shares one public address: use a broad IP ceiling and
            # a separate narrow identity budget, also effective across IPs.
            ip_key = private_key('ip-auth' if shared_auth else 'ip-public', peer)
            client = None
            try:
                from starlette.concurrency import run_in_threadpool
                client = Redis.from_url(settings.REDIS_URL, socket_timeout=1, socket_connect_timeout=1)
                counts = await run_in_threadpool(client.eval, RATE_SCRIPT, 1, ip_key)
                ip_limit = settings.AUTH_RATE_LIMIT_PER_IP_MINUTE if shared_auth else settings.AUTH_RATE_LIMIT_PER_MINUTE
                if counts[0] > ip_limit:
                    return JSONResponse({"detail": "Слишком много попыток. Повторите через минуту.", "request_id": request_id}, status_code=429, headers={"Retry-After": "60", "X-Request-ID": request_id})
                identity = await account_bucket(request)
                if identity:
                    counts = await run_in_threadpool(client.eval, RATE_SCRIPT, 1, identity)
                    if counts[0] > settings.AUTH_RATE_LIMIT_PER_MINUTE:
                        return JSONResponse({"detail": "Слишком много попыток. Повторите через минуту.", "request_id": request_id}, status_code=429, headers={"Retry-After": "60", "X-Request-ID": request_id})
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail, "request_id": request_id}, status_code=exc.status_code, headers={"X-Request-ID": request_id, "Cache-Control": "no-store"})
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
