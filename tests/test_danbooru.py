import asyncio

import httpx
import pytest
from helpers import collect, mock_client, tags_of

from app.sources import danbooru
from app.sources.danbooru import PAGE_SIZE, DanbooruSource


def post(id, **fields):
    return {
        "id": id,
        "file_url": f"http://cdn.test/{id}.png",
        "large_file_url": f"http://cdn.test/{id}.webm",
        "file_ext": "png",
        "tag_string": "cat an_artist",
        "tag_string_general": "cat",
        "tag_string_artist": "an_artist",
        "tag_string_copyright": "",
        "tag_string_character": "",
        "tag_string_meta": "",
        "image_width": 10,
        "image_height": 20,
        **fields,
    }


def use_danbooru(monkeypatch, handler):
    monkeypatch.setattr(danbooru, "session", mock_client(handler))


def test_search_pages_by_id_and_normalises_posts(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request.url.params)
        if len(requests) == 1:
            return httpx.Response(200, json=[post(id) for id in range(1000, 1000 - PAGE_SIZE, -1)])
        return httpx.Response(200, json=[post(5)])

    use_danbooru(monkeypatch, handler)
    posts = collect(DanbooruSource(), tags="cat", rating="sketchy")

    assert requests[0]["tags"] == "cat rating:s,q"
    assert requests[1]["page"] == f"b{1001 - PAGE_SIZE}"
    assert len(posts) == PAGE_SIZE + 1
    assert posts[0]["tags"] == "cat an_artist"
    assert (posts[0]["width"], posts[0]["height"]) == (10, 20)


def test_order_searches_use_numbered_pages(monkeypatch):
    pages = []

    def handler(request):
        pages.append(request.url.params["page"])
        return httpx.Response(200, json=[post(id) for id in range(PAGE_SIZE if len(pages) == 1 else 1)])

    use_danbooru(monkeypatch, handler)
    collect(DanbooruSource(), tags="cat order:score")

    assert pages == ["1", "2"]


def test_blacklist_is_applied_locally_and_restricted_posts_are_dropped(monkeypatch):
    posts = [post(1), post(2, tag_string="cat dog"), post(3, file_url=None)]
    del posts[2]["file_url"]
    tags_sent = []

    def handler(request):
        tags_sent.append(request.url.params["tags"])
        return httpx.Response(200, json=posts)

    use_danbooru(monkeypatch, handler)
    results = collect(DanbooruSource(), tags="cat", blacklist_tags="dog")

    assert [p["id"] for p in results] == [1]
    assert tags_sent == ["cat"]


def test_ugoira_uses_the_video_file(monkeypatch):
    use_danbooru(monkeypatch, lambda request: httpx.Response(200, json=[post(1, file_ext="zip")]))

    assert collect(DanbooruSource())[0]["file_url"] == "http://cdn.test/1.webm"


def test_tag_types_come_from_the_post(monkeypatch):
    use_danbooru(monkeypatch, lambda request: httpx.Response(200, json=[post(1)]))
    source = DanbooruSource()
    collect(source)

    tags = tags_of(source, ["cat", "an_artist", "unseen"])

    assert {t["name"]: t["category"] for t in tags} == {
        "cat": "general",
        "an_artist": "artist",
        "unseen": "general",
    }


def test_tag_limit_errors_explain_themselves(monkeypatch):
    use_danbooru(monkeypatch, lambda request: httpx.Response(422))

    with pytest.raises(RuntimeError, match="2 tags"):
        collect(DanbooruSource(), tags="a b c")


def test_notes_come_through_as_markdown(monkeypatch):
    note = {"x": 1, "y": 2, "width": 3, "height": 4, "body": "<b>hi</b>", "is_active": True}
    use_danbooru(monkeypatch, lambda request: httpx.Response(200, json=[note, {**note, "is_active": False}]))
    source = DanbooruSource()

    assert asyncio.run(source.grab_notes({"id": 1})) is None
    assert asyncio.run(source.grab_notes({"id": 1, "last_noted_at": "2026-01-01"})) == [
        {"x": 1, "y": 2, "width": 3, "height": 4, "body": "**hi**"}
    ]
