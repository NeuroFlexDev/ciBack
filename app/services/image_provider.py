"""Bounded provider image responses; URL downloads never follow redirects or carry credentials."""
from __future__ import annotations

import base64
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

from app.core.config import settings


class ImageResultError(ValueError):
    pass


def generation_options(model: str) -> dict:
    mode = settings.IMAGE_API_MODE
    if mode == "auto":
        mode = "gpt_image" if settings.IMAGE_PROVIDER == "vsellm" or model.rsplit("/", 1)[-1].startswith("gpt-image-") else "openai"
    common = {"model": model, "n": 1, "size": "1024x1024"}
    # GPT Image always returns base64 and rejects response_format. Keep the
    # priced quality explicit; never silently upgrade to a more expensive tier.
    if mode == "gpt_image":
        return common | {"quality": settings.IMAGE_QUALITY}
    return common | {"response_format": "b64_json"}


def public_addresses(host: str) -> list[str]:
    addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ImageResultError("image_url_blocked")
    return addresses


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the already validated numeric IP, but verify the real host
        # certificate and SNI. A second DNS lookup cannot redirect to localhost.
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def download_image(url: str, *, timeout: int, allowed_hosts: list[str] | None = None) -> bytes:
    if len(url) > 16000 or any(ord(char) < 32 for char in url):
        raise ImageResultError("image_url_blocked")
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        # A previously admitted job cannot gain a newly added CDN destination.
        allowed = {entry.lower() for entry in settings.IMAGE_DOWNLOAD_HOSTS}
        if allowed_hosts is not None:
            allowed &= {entry.lower() for entry in allowed_hosts}
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment or parsed.port not in {None, 443} or host not in allowed:
            raise ImageResultError("image_url_blocked")
        addresses = public_addresses(host)
    except (ValueError, OSError) as exc:
        raise ImageResultError("image_url_blocked") from exc
    connection = PinnedHTTPSConnection(host, addresses[0], min(timeout, 60))
    try:
        # No Authorization/Cookie, proxy environment, redirects, decompression,
        # user-provided headers, file:// or external URL from lesson content.
        connection.request("GET", (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
                           headers={"Accept": "image/png,image/jpeg,image/webp", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        if response.status != 200 or response.getheader("Content-Encoding", "identity") != "identity":
            raise ImageResultError("image_download_failed")
        length = response.getheader("Content-Length")
        if length and (not length.isdigit() or int(length) > settings.IMAGE_MAX_BYTES):
            raise ImageResultError("invalid_image_response")
        content = response.read(settings.IMAGE_MAX_BYTES + 1)
        if not content or len(content) > settings.IMAGE_MAX_BYTES:
            raise ImageResultError("invalid_image_response")
        return content
    finally:
        connection.close()


def image_bytes(result, *, timeout: int, allowed_hosts: list[str] | None = None) -> bytes:
    if not result.data or len(result.data) != 1:
        raise ImageResultError("invalid_image_response")
    item = result.data[0]
    encoded = getattr(item, "b64_json", None)
    if encoded:
        if len(encoded) > settings.IMAGE_MAX_BYTES * 4 // 3 + 16:
            raise ImageResultError("invalid_image_response")
        try:
            return base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ImageResultError("invalid_image_response") from exc
    url = getattr(item, "url", None)
    if url:
        return download_image(url, timeout=timeout, allowed_hosts=allowed_hosts)
    raise ImageResultError("invalid_image_response")
