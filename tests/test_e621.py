import asyncio

import httpx
from helpers import collect, mock_client, tags_of

from app.sources import e621
from app.sources.e621 import PAGE_SIZE, E621Source


def post(id, url="http://static.test/a.png", **fields):
    return {
        "id": id,
        "file": {"url": url, "md5": f"md5-{id}", "width": 10, "height": 20, "ext": "png"},
        "tags": {
            "general": ["cat"],
            "artist": ["an_artist"],
            "contributor": ["a_helper"],
            "copyright": [],
            "character": [],
            "species": ["felid"],
            "invalid": [],
            "meta": ["hi_res"],
            "lore": [],
        },
        "sources": ["http://one.test", "http://two.test"],
        "has_notes": False,
        "rating": "s",
        **fields,
    }


def use_e621(monkeypatch, handler):
    monkeypatch.setattr(e621, "session", mock_client(handler))


def test_search_pages_by_id_and_normalises_posts(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request.url.params)
        count = PAGE_SIZE if len(requests) == 1 else 1
        return httpx.Response(200, json={"posts": [post(1000 - i) for i in range(count)]})

    use_e621(monkeypatch, handler)
    posts = collect(E621Source(), tags="cat", blacklist_tags="dog", rating="sketchy")

    assert requests[0]["tags"] == "-dog cat rating:q"
    assert requests[1]["page"] == f"b{1001 - PAGE_SIZE}"
    assert posts[0]["md5"] == "md5-1000"
    assert posts[0]["file_url"] == "http://static.test/a.png"
    assert (posts[0]["width"], posts[0]["height"]) == (10, 20)
    assert posts[0]["tags"].split() == ["cat", "an_artist", "a_helper", "felid", "hi_res"]
    assert posts[0]["source"] == "http://one.test\nhttp://two.test"


def test_order_searches_use_numbered_pages(monkeypatch):
    pages = []

    def handler(request):
        pages.append(request.url.params["page"])
        count = PAGE_SIZE if len(pages) == 1 else 1
        return httpx.Response(200, json={"posts": [post(i) for i in range(count)]})

    use_e621(monkeypatch, handler)
    collect(E621Source(), tags="cat order:score")

    assert pages == ["1", "2"]


def test_posts_without_a_file_are_dropped(monkeypatch):
    use_e621(monkeypatch, lambda request: httpx.Response(200, json={"posts": [post(1), post(2, url=None)]}))

    assert [p["id"] for p in collect(E621Source())] == [1]


def test_tag_groups_map_onto_szurubooru_categories(monkeypatch):
    use_e621(monkeypatch, lambda request: httpx.Response(200, json={"posts": [post(1)]}))
    source = E621Source()
    collect(source)

    tags = tags_of(source, ["cat", "an_artist", "a_helper", "felid", "hi_res"])

    assert {t["name"]: t["category"] for t in tags} == {
        "cat": "general",
        "an_artist": "artist",
        "a_helper": "artist",
        "felid": "species",
        "hi_res": "metadata",
    }


def test_notes_are_only_requested_for_posts_that_have_them(monkeypatch):
    note = {"x": 1, "y": 2, "width": 3, "height": 4, "body": "<b>hi</b>", "is_active": True}
    use_e621(monkeypatch, lambda request: httpx.Response(200, json=[note]))
    source = E621Source()

    assert asyncio.run(source.grab_notes({"id": 1, "has_notes": False})) is None
    assert asyncio.run(source.grab_notes({"id": 1, "has_notes": True})) == [
        {"x": 1, "y": 2, "width": 3, "height": 4, "body": "**hi**"}
    ]
