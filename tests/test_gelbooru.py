import asyncio
import logging

import httpx

from app import http
from app.sources.gelbooru import GelbooruSource, html_note_to_markdown


def post(id):
    return {"id": id, "tags": "a b&#039;c", "has_notes": "false"}


def gelbooru_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), params={"api_key": "secret-key"})


def test_search_paginates_up_to_the_limit(monkeypatch):
    pages = []

    def handler(request):
        pid = int(request.url.params["pid"])
        pages.append(pid)
        return httpx.Response(
            200,
            json={"@attributes": {"count": 250}, "post": [post(pid * 100 + i) for i in range(100)]},
        )

    monkeypatch.setattr(http, "gelbooru_session", gelbooru_client(handler))
    results = asyncio.run(GelbooruSource().search_posts(limit=150, tags="cat"))

    assert pages == [0, 1]
    assert len(results) == 150
    assert results[0]["tags"] == "a b'c"


def test_note_markup_becomes_markdown():
    assert html_note_to_markdown("<b>bold</b><br>a_b<span>c</span>") == "**bold**\na\\_bc"


def test_requests_do_not_log_the_api_key(monkeypatch, caplog):
    import app.main  # noqa: F401

    def handler(request):
        return httpx.Response(200, json={"@attributes": {"count": 0}})

    monkeypatch.setattr(http, "gelbooru_session", gelbooru_client(handler))
    with caplog.at_level(logging.INFO):
        asyncio.run(GelbooruSource().search_posts(limit=1))

    assert "secret-key" not in caplog.text
