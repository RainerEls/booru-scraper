import asyncio
import logging

import httpx
import pytest

from app import http
from app.sources.base import html_note_to_markdown
from app.sources.gelbooru import GelbooruSource


def post(id):
    return {"id": id, "tags": "a b&#039;c", "has_notes": "false"}


def gelbooru_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), params={"api_key": "secret-key"})


async def collect(posts, count):
    results = []
    async for post in posts:
        results.append(post)
        if len(results) == count:
            break
    return results


def gelbooru_with_posts(count, requests):
    """Answer like Gelbooru: newest first, honouring id:< and refusing deep offsets."""
    newest = count

    def handler(request):
        tags = request.url.params["tags"]
        requests.append(tags)
        below = next((int(t[4:]) for t in tags.split() if t.startswith("id:<")), newest + 1)
        offset = int(request.url.params["pid"]) * 100
        if offset > 20_000:
            return httpx.Response(200, text="Too deep! Pull it back some.")
        ids = range(below - 1 - offset, max(below - 1 - offset - 100, 0), -1)
        return httpx.Response(200, json={"post": [post(id) for id in ids]})

    return handler


def test_search_fetches_only_the_pages_consumed(monkeypatch):
    requests = []
    monkeypatch.setattr(http, "gelbooru_session", gelbooru_client(gelbooru_with_posts(250, requests)))

    results = asyncio.run(collect(GelbooruSource().search_posts(tags="cat"), 150))

    assert requests == ["cat", "cat id:<151"]
    assert [p["id"] for p in results] == list(range(250, 100, -1))
    assert results[0]["tags"] == "a b'c"


def test_search_continues_past_the_offset_cap(monkeypatch):
    requests = []
    monkeypatch.setattr(http, "gelbooru_session", gelbooru_client(gelbooru_with_posts(25_050, requests)))

    results = asyncio.run(collect(GelbooruSource().search_posts(tags="cat"), 100_000))

    assert [p["id"] for p in results] == list(range(25_050, 0, -1))


def test_sorted_search_pages_by_offset_up_to_the_cap(monkeypatch):
    requests = []
    monkeypatch.setattr(http, "gelbooru_session", gelbooru_client(gelbooru_with_posts(25_050, requests)))

    results = asyncio.run(collect(GelbooruSource().search_posts(tags="cat sort:score"), 100_000))

    assert len(results) == 20_100
    assert set(requests) == {"cat sort:score"}


def test_note_markup_becomes_markdown():
    assert html_note_to_markdown("<b>bold</b><br>a_b<span>c</span>") == "**bold**\na\\_bc"


def test_requests_do_not_log_the_api_key(monkeypatch, caplog):
    import app.main  # noqa: F401

    def handler(request):
        return httpx.Response(200, json={"@attributes": {"count": 0}})

    monkeypatch.setattr(http, "gelbooru_session", gelbooru_client(handler))
    with caplog.at_level(logging.INFO):
        asyncio.run(collect(GelbooruSource().search_posts(), 1))

    assert "secret-key" not in caplog.text


def test_tag_lookup_with_no_matches_returns_nothing(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"@attributes": {"count": 0}})

    monkeypatch.setattr(http, "gelbooru_session", gelbooru_client(handler))
    assert asyncio.run(GelbooruSource().grab_tags({"unknown"})) == []


def test_http_errors_do_not_expose_the_api_key(monkeypatch):
    def handler(request):
        return httpx.Response(429)

    monkeypatch.setattr(http, "gelbooru_session", gelbooru_client(handler))
    with pytest.raises(RuntimeError) as e:
        asyncio.run(collect(GelbooruSource().search_posts(), 1))

    assert "429" in str(e.value)
    assert "secret-key" not in str(e.value)


def test_search_stops_after_a_short_page(monkeypatch):
    pages = []

    def handler(request):
        pages.append(request.url.params["pid"])
        return httpx.Response(200, json={"post": [post(1)]})

    monkeypatch.setattr(http, "gelbooru_session", gelbooru_client(handler))
    assert len(asyncio.run(collect(GelbooruSource().search_posts(), 100))) == 1
    assert pages == ["0"]


def test_notes_are_only_requested_for_posts_that_have_them(monkeypatch):
    def handler(request):
        return httpx.Response(200, text='<notes><note x="1" y="2" width="3" height="4" body="hi"/></notes>')

    monkeypatch.setattr(http, "gelbooru_session", gelbooru_client(handler))
    source = GelbooruSource()

    assert asyncio.run(source.grab_notes({"id": 1, "has_notes": "false"})) is None
    assert asyncio.run(source.grab_notes({"id": 1, "has_notes": "true"})) == [
        {"x": 1, "y": 2, "width": 3, "height": 4, "body": "hi"}
    ]
