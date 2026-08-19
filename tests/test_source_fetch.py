"""Public Work source capture stays bounded and SSRF-safe."""
from __future__ import annotations

import asyncio

import pytest

from cc_remote.wrapper import source_fetch


class _Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes = b""):
        self.status_code = status
        self.headers = headers
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        yield self._body


class _Client:
    def __init__(self, responses: list[_Response]):
        self.responses = responses
        self.requested: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def stream(self, _method: str, url: str):
        self.requested.append(url)
        return self.responses.pop(0)


def test_capture_converts_visible_html_without_script_content(monkeypatch):
    client = _Client([_Response(
        200,
        {"content-type": "text/html; charset=utf-8"},
        b"<h1>Title</h1><script>secret()</script><p>Hello world</p>",
    )])
    checked: list[str] = []

    async def validate(url: str):
        checked.append(url)

    monkeypatch.setattr(source_fetch, "_validate_url", validate)
    monkeypatch.setattr(source_fetch, "_public_peer", lambda _response: None)
    monkeypatch.setattr(source_fetch.httpx, "AsyncClient", lambda **_kwargs: client)

    filename, content = asyncio.run(
        source_fetch.capture_public_source("https://example.test/page"))

    assert filename == "网页摘录.md"
    assert checked == ["https://example.test/page"]
    assert client.requested == checked
    text = content.decode()
    assert "Title" in text and "Hello world" in text
    assert "secret()" not in text


def test_redirect_target_is_revalidated_before_second_connection(monkeypatch):
    client = _Client([_Response(302, {"location": "http://127.0.0.1/admin"})])
    checked: list[str] = []

    async def validate(url: str):
        checked.append(url)
        if "127.0.0.1" in url:
            raise ValueError("链接不能指向本机、内网或保留地址")

    monkeypatch.setattr(source_fetch, "_validate_url", validate)
    monkeypatch.setattr(source_fetch, "_public_peer", lambda _response: None)
    monkeypatch.setattr(source_fetch.httpx, "AsyncClient", lambda **_kwargs: client)

    with pytest.raises(ValueError, match="内网"):
        asyncio.run(source_fetch.capture_public_source("https://example.test/start"))

    assert checked == [
        "https://example.test/start",
        "http://127.0.0.1/admin",
    ]
    assert client.requested == ["https://example.test/start"]


def test_markdown_capture_enforces_decoded_output_bound():
    content = source_fetch._markdown_capture(
        "https://example.test/large",
        "text/plain; charset=utf-8",
        ("数据" * source_fetch._MAX_CAPTURE_BYTES).encode(),
    )
    assert len(content) <= source_fetch._MAX_CAPTURE_BYTES
    assert content.endswith("…（网页正文已截断）\n".encode())
