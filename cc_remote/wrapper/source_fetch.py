"""Safe, bounded capture of public HTTP(S) sources for isolated Work."""
from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

_MAX_CAPTURE_BYTES = 2 * 1024 * 1024
_MAX_REDIRECTS = 5
_ALLOWED_TYPES = (
    "text/", "application/json", "application/ld+json",
    "application/xml", "application/xhtml+xml",
)


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "template"}:
            self._hidden += 1
        elif not self._hidden and tag in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "template"}:
            self._hidden = max(0, self._hidden - 1)
        elif not self._hidden and tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._hidden:
            self.parts.append(data)

    def text(self) -> str:
        value = html.unescape("".join(self.parts))
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n\s*\n\s*\n+", "\n\n", value)
        return value.strip()


def _public_addresses(host: str, port: int) -> None:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = {item[4][0].split("%", 1)[0] for item in infos}
    if not addresses:
        raise ValueError("链接域名无法解析")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError("链接解析结果无效") from exc
        if not address.is_global:
            raise ValueError("链接不能指向本机、内网或保留地址")


def _public_peer(response: httpx.Response) -> None:
    stream = response.extensions.get("network_stream")
    get_extra_info = getattr(stream, "get_extra_info", None)
    peer = get_extra_info("server_addr") if callable(get_extra_info) else None
    host = peer[0].split("%", 1)[0] if isinstance(peer, tuple) and peer else None
    try:
        address = ipaddress.ip_address(host) if host else None
    except ValueError as exc:
        raise ValueError("无法确认链接的远端地址") from exc
    if address is None or not address.is_global:
        raise ValueError("链接实际连接到了本机、内网或保留地址")


async def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("资料链接必须是公开的 HTTP(S) 地址")
    if parsed.username or parsed.password:
        raise ValueError("资料链接不能包含账号或密码")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    await asyncio.wait_for(
        asyncio.to_thread(_public_addresses, parsed.hostname, port), 5.0)


def _decode(body: bytes, content_type: str) -> str:
    charset = None
    match = re.search(r"charset=([^;\s]+)", content_type, re.I)
    if match:
        charset = match.group(1).strip('"\'')[:64]
    for candidate in (charset, "utf-8", "gb18030"):
        if not candidate:
            continue
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    raise ValueError("链接内容不是可读取的文本")


def _markdown_capture(url: str, content_type: str, body: bytes) -> bytes:
    text = _decode(body, content_type)
    base_type = content_type.split(";", 1)[0].strip().lower()
    if base_type in {"text/html", "application/xhtml+xml"}:
        parser = _VisibleText()
        parser.feed(text)
        text = parser.text()
    elif base_type in {"application/json", "application/ld+json"}:
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass
    text = text.strip()
    if not text:
        raise ValueError("链接没有可读取的正文")
    result = f"来源：{url}\n\n{text}\n".encode("utf-8")
    if len(result) > _MAX_CAPTURE_BYTES:
        marker = "\n\n…（网页正文已截断）\n".encode("utf-8")
        result = result[:_MAX_CAPTURE_BYTES - len(marker)].decode(
            "utf-8", errors="ignore").encode("utf-8") + marker
    return result


async def capture_public_source(url: str) -> tuple[str, bytes]:
    current = url.strip()
    timeout = httpx.Timeout(15.0, connect=8.0)
    async with httpx.AsyncClient(
        follow_redirects=False, trust_env=False, timeout=timeout,
        headers={"User-Agent": "cc-remote-work/1", "Accept": "text/html,text/plain,application/json,application/xml;q=0.9,*/*;q=0.1"},
    ) as client:
        try:
            for _ in range(_MAX_REDIRECTS + 1):
                await _validate_url(current)
                async with client.stream("GET", current) as response:
                    _public_peer(response)
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("链接重定向缺少目标")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    base_type = content_type.split(";", 1)[0].strip()
                    if not any(base_type.startswith(prefix) for prefix in _ALLOWED_TYPES):
                        raise ValueError("链接不是支持的文本、HTML、JSON 或 XML 内容")
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > _MAX_CAPTURE_BYTES:
                        raise ValueError("链接内容超过 2 MiB")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > _MAX_CAPTURE_BYTES:
                            raise ValueError("链接内容超过 2 MiB")
                        chunks.append(chunk)
                    return "网页摘录.md", _markdown_capture(
                        current, content_type, b"".join(chunks))
        except httpx.HTTPStatusError as exc:
            raise ValueError(f"链接返回 HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise ValueError("链接读取失败") from exc
        raise ValueError("链接重定向次数过多")
