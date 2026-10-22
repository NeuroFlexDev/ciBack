"""Bound outbound requests to an approved origin and a validated DNS result."""
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit
import httpx
from app.core.config import settings


def origin(url):
    parsed = urlsplit(str(url))
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


def destination(url, *, local=False):
    parsed = urlsplit(str(url))
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Нужен адрес API без логина, пароля, параметров и фрагмента.")
    if local:
        approved = {origin(item) for item in settings.AI_LOCAL_ENDPOINTS}
        approved.update(origin(item.base_url) for item in settings.AI_PROVIDERS if item.local)
        if origin(url) not in approved:
            raise ValueError("Адрес локальной модели сначала должен быть разрешён в AI_LOCAL_ENDPOINTS на сервере.")
    elif parsed.scheme != "https" or parsed.port not in (None, 443):
        raise ValueError("Для внешнего провайдера нужен HTTPS на порту 443.")
    port = origin(url)[2]
    addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)))
    if not addresses or (not local and any(not ipaddress.ip_address(ip).is_global for ip in addresses)):
        raise ValueError("Внешний API не может вести во внутреннюю или служебную сеть.")
    return parsed, addresses[0], port


class PinnedTransport(httpx.BaseTransport):
    """Stateless transport: every connection is closed before returning a response."""
    def __init__(self, provider):
        self.provider = provider

    def handle_request(self, request):
        base = urlsplit(str(self.provider.base_url))
        if origin(request.url) != origin(self.provider.base_url) or not request.url.path.startswith(base.path.rstrip("/") + "/"):
            raise httpx.RequestError("AI request destination changed", request=request)
        try:
            parsed, address, port = destination(str(request.url), local=self.provider.local)
        except (ValueError, OSError):
            raise httpx.ConnectError("AI endpoint is not allowed or cannot be resolved", request=request) from None
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=self.provider.timeout_seconds)
        try:
            sock = socket.create_connection((address, port), self.provider.timeout_seconds)
            connection.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname) if parsed.scheme == "https" else sock
            headers = dict(request.headers)
            headers["accept-encoding"] = "identity"
            connection.request(request.method, parsed.path or "/", body=request.read(), headers=headers)
            response = connection.getresponse()
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise httpx.ReadError("Compressed AI response is not allowed", request=request)
            content = response.read(16 * 1024 * 1024 + 1)
            if len(content) > 16 * 1024 * 1024:
                raise httpx.ReadError("AI response exceeds the size limit", request=request)
            # Never follow redirects with an API credential.
            return httpx.Response(response.status, headers=response.getheaders(), content=content, request=request)
        except (OSError, http.client.HTTPException):
            raise httpx.ConnectError("AI connection failed", request=request) from None
        finally:
            connection.close()


def client(provider):
    return httpx.Client(transport=PinnedTransport(provider), follow_redirects=False,
                        trust_env=False, timeout=provider.timeout_seconds)
