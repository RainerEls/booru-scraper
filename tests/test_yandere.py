import asyncio

import httpx

from app.sources import yandere
from app.sources.yandere import YandereSource


def page(posts, tags=None):
    return httpx.Response(200, json={"posts": posts, "tags": tags or {}})


def use_yandere(monkeypatch, handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), params={"password_hash": "secret"})
    monkeypatch.setattr(yandere, "session", client)


def collect(source):
    async def run():
        return [post async for post in source.search_posts(tags="cat", rating="safe")]

    return asyncio.run(run())


def test_search_builds_the_query_and_stops_at_a_short_page(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request.url.params)
        return page([{"id": 1, "tags": "a b&#039;c"}])

    use_yandere(monkeypatch, handler)
    posts = collect(YandereSource())

    assert posts[0]["tags"] == "a b'c"
    assert len(requests) == 1
    assert requests[0]["page"] == "1"
    assert requests[0]["tags"] == "cat rating:s"


def test_tag_types_come_from_the_search_results(monkeypatch):
    use_yandere(
        monkeypatch,
        lambda request: page([{"id": 1, "tags": "a"}], {"a_circle": "circle", "an_artist": "artist"}),
    )
    source = YandereSource()
    collect(source)

    tags = asyncio.run(source.grab_tags(["a_circle", "an_artist", "unseen"]))

    categories = {tag["name"]: tag["category"] for tag in tags}
    assert categories == {"a_circle": "copyright", "an_artist": "artist", "unseen": "general"}


def test_notes_keep_active_visible_ones(monkeypatch):
    note = {"x": 1, "y": 2, "width": 3, "height": 4, "body": "<b>hi</b>", "is_active": True}

    def handler(request):
        return httpx.Response(200, json=[note, {**note, "is_active": False}, {**note, "width": 0}])

    use_yandere(monkeypatch, handler)
    source = YandereSource()

    assert asyncio.run(source.grab_notes({"id": 1, "last_noted_at": 0})) is None
    assert asyncio.run(source.grab_notes({"id": 1, "last_noted_at": 5})) == [
        {"x": 1, "y": 2, "width": 3, "height": 4, "body": "**hi**"}
    ]
