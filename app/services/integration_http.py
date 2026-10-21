"""Bounded HTTPS requests pinned to validated DNS results. No redirects or proxies."""
import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit
from app.core.integration_config import integration_settings


def validate_endpoint(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Integration endpoints require HTTPS without credentials or fragments")
    if parsed.port not in (None, 443):
        raise ValueError("Integration endpoints must use port 443")
    allowed_private = {h.strip().lower() for h in integration_settings.INTEGRATION_PRIVATE_HOSTS.split(",") if h.strip()}
    addresses = list({item[4][0] for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)})
    if not addresses:
        raise ValueError("Integration hostname has no address")
    if parsed.hostname.lower() not in allowed_private and any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("Private, loopback and reserved integration destinations are not permitted")
    return parsed, addresses[0]


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def request(method, url, body=None, headers=None, max_bytes=1024 * 1024):
    parsed, address = validate_endpoint(url)
    connection = PinnedHTTPSConnection(parsed.hostname, address, integration_settings.OIDC_HTTP_TIMEOUT)
    try:
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        content = response.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ValueError("Integration response exceeds limit")
        if response.status >= 300:
            raise RuntimeError(f"remote_http_{response.status}")
        return content
    finally:
        connection.close()


def request_json(method, url, body=None, headers=None):
    return json.loads(request(method, url, body, headers))
