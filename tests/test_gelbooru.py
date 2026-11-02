import asyncio
import logging

import httpx2
import pytest
from helpers import collect, mock_client, tags_of

from app.http import ApiError
from app.sources import gelbooru
from app.sources.base import html_note_to_markdown
from app.sources.gelbooru import GelbooruSource


def post(id):
    return {"id": id, "tags": "a b&#039;c", "has_notes": "false"}


def gelbooru_client(handler):
    return mock_client(handler, params={"api_key": "secret-key"})


def gelbooru_with_posts(count, requests):
    """Answer like Gelbooru: newest first, honouring id:< and refusing deep offsets."""
    newest = count

    def handler(request):
        tags = request.url.params["tags"]
        requests.append(tags)
        below = next((int(t[4:]) for t in tags.split() if t.startswith("id:<")), newest + 1)
        offset = int(request.url.params["pid"]) * 100
        if offset > 20_000:
            return httpx2.Response(200, text="Too deep! Pull it back some.")
        ids = range(below - 1 - offset, max(below - 1 - offset - 100, 0), -1)
        return httpx2.Response(200, json={"post": [post(id) for id in ids]})

    return handler


def test_search_fetches_only_the_pages_consumed(monkeypatch):
    requests = []
    monkeypatch.setattr(gelbooru, "session", gelbooru_client(gelbooru_with_posts(250, requests)))

    results = collect(GelbooruSource(), 150, tags="cat")

    assert requests == ["cat", "cat id:<151"]
    assert [p["id"] for p in results] == list(range(250, 100, -1))
    assert results[0]["tags"] == "a b'c"


def test_search_continues_past_the_offset_cap(monkeypatch):
    requests = []
    monkeypatch.setattr(gelbooru, "session", gelbooru_client(gelbooru_with_posts(25_050, requests)))

    results = collect(GelbooruSource(), 100_000, tags="cat")

    assert [p["id"] for p in results] == list(range(25_050, 0, -1))


def test_other_sorts_page_by_offset_up_to_the_cap(monkeypatch):
    requests = []
    monkeypatch.setattr(gelbooru, "session", gelbooru_client(gelbooru_with_posts(25_050, requests)))

    results = collect(GelbooruSource(), 100_000, tags="cat sort:updated")

    assert len(results) == 20_100
    assert set(requests) == {"cat sort:updated"}


def gelbooru_with_scores(scores):
    """Answer like Gelbooru for score sorts: ties in no id order, score: and id:< filters applied."""
    all_posts = [{**post(id), "score": score} for id, score in enumerate(scores, start=1)]

    def handler(request):
        terms = request.url.params["tags"].split()
        matches = all_posts
        ordered = False
        for term in terms:
            name, _, value = term.partition(":")
            if name == "score" and value[0] in "<>":
                bound = int(value[1:])
                matches = [p for p in matches if (p["score"] < bound if value[0] == "<" else p["score"] > bound)]
            elif name == "score":
                matches = [p for p in matches if p["score"] == int(value)]
            elif name == "id":
                matches = [p for p in matches if p["id"] < int(value[1:])]
            elif name == "sort":
                ordered = value.endswith("asc") and "asc" or "desc"
        if ordered:
            tie_order = sorted(matches, key=lambda p: (p["id"] * 7919) % 1009)
            matches = sorted(tie_order, key=lambda p: p["score"], reverse=ordered == "desc")
        else:
            matches = sorted(matches, key=lambda p: -p["id"])
        offset = int(request.url.params["pid"]) * 100
        if offset > 200:
            return httpx2.Response(200, text="Too deep! Pull it back some.")
        return httpx2.Response(200, json={"post": matches[offset : offset + 100]})

    return handler


@pytest.mark.parametrize(
    "sort_term, ordering",
    [("sort:score", -1), ("sort:score:desc", -1), ("sort:score:asc", 1)],
)
def test_score_sort_reaches_every_post_once(monkeypatch, sort_term, ordering):
    # 450 tied posts outnumber the 300 the fake allows by offset.
    scores = [50 - i for i in range(50)] + [0] * 450 + [-1] * 120
    monkeypatch.setattr(gelbooru, "MAX_OFFSET_PAGES", 3)
    monkeypatch.setattr(gelbooru, "session", gelbooru_client(gelbooru_with_scores(scores)))

    results = collect(GelbooruSource(), 100_000, tags=sort_term)

    assert sorted(p["id"] for p in results) == list(range(1, len(scores) + 1))
    assert [p["score"] for p in results] == sorted(scores, key=lambda score: ordering * score)


@pytest.mark.parametrize(
    "rating, terms",
    [
        ("safe", "rating:general"),
        ("sketchy", "-rating:general -rating:explicit"),
        ("unsafe", "rating:explicit"),
    ],
)
def test_ratings_map_onto_gelbooru_terms(monkeypatch, rating, terms):
    requests = []
    monkeypatch.setattr(gelbooru, "session", gelbooru_client(gelbooru_with_posts(1, requests)))

    collect(GelbooruSource(), 1, tags="cat", rating=rating)

    assert requests == [f"cat {terms}"]


def test_note_markup_becomes_markdown():
    assert html_note_to_markdown("<b>bold</b><br>a_b<span>c</span>") == "**bold**\na\\_bc"


def test_requests_do_not_log_the_api_key(monkeypatch, caplog):
    import app.main  # noqa: F401

    def handler(request):
        return httpx2.Response(200, json={"@attributes": {"count": 0}})

    monkeypatch.setattr(gelbooru, "session", gelbooru_client(handler))
    with caplog.at_level(logging.INFO):
        collect(GelbooruSource(), 1)

    assert "secret-key" not in caplog.text


def test_tag_lookup_with_no_matches_returns_nothing(monkeypatch):
    def handler(request):
        return httpx2.Response(200, json={"@attributes": {"count": 0}})

    monkeypatch.setattr(gelbooru, "session", gelbooru_client(handler))
    assert tags_of(GelbooruSource(), {"unknown"}) == []


def test_http_errors_do_not_expose_the_api_key(monkeypatch):
    def handler(request):
        return httpx2.Response(429)

    monkeypatch.setattr(gelbooru, "session", gelbooru_client(handler))
    with pytest.raises(ApiError) as e:
        collect(GelbooruSource(), 1)

    assert "429" in str(e.value)
    assert "secret-key" not in str(e.value)


def test_search_stops_after_a_short_page(monkeypatch):
    pages = []

    def handler(request):
        pages.append(request.url.params["pid"])
        return httpx2.Response(200, json={"post": [post(1)]})

    monkeypatch.setattr(gelbooru, "session", gelbooru_client(handler))
    assert len(collect(GelbooruSource(), 100)) == 1
    assert pages == ["0"]


def test_notes_are_only_requested_for_posts_that_have_them(monkeypatch):
    def handler(request):
        return httpx2.Response(200, text='<notes><note x="1" y="2" width="3" height="4" body="hi"/></notes>')

    monkeypatch.setattr(gelbooru, "session", gelbooru_client(handler))
    source = GelbooruSource()

    assert asyncio.run(source.grab_notes({"id": 1, "has_notes": "false"})) is None
    assert asyncio.run(source.grab_notes({"id": 1, "has_notes": "true"})) == [
        {"x": 1, "y": 2, "width": 3, "height": 4, "body": "hi"}
    ]
