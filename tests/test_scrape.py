import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import http, main
from app.sources.base import BooruSource


class FakeDb:
    def __init__(self):
        self.posts = {}
        self.tags = set()
        self.runs = {}

    async def create_run(self, uuid, *args):
        self.runs[1] = "running"
        return 1

    async def update_run(self, id, total, processed, skipped, failed, status):
        self.runs[id] = status

    async def post_exists(self, source, source_post_id):
        return (source, source_post_id) in self.posts

    async def md5_exists(self, md5):
        return any(p["md5"] == md5 for p in self.posts.values())

    async def claim_post(self, source, source_post_id, md5, image_url, status):
        self.posts[(source, source_post_id)] = {"md5": md5, "status": status}
        return (source, source_post_id)

    async def release_claims(self, post_ids):
        for id in post_ids:
            if self.posts.get(id, {}).get("status") == "queued":
                del self.posts[id]

    async def tag_exists(self, tag_name):
        return tag_name in self.tags

    async def store_tag(self, tag_name, tag_type):
        self.tags.add(tag_name)

    async def add_szurubooru_post_id(self, id, szurubooru_post_id):
        pass

    async def update_post_status(self, source, source_post_id, status):
        self.posts[(source, source_post_id)]["status"] = status


class FakeSource(BooruSource):
    def __init__(self, posts, tags=()):
        self.posts = posts
        self.tags = tags
        self.yielded = 0
        self.notes_for = []

    async def search_posts(self, tags="", blacklist_tags="", rating=None):
        for post in self.posts:
            self.yielded += 1
            yield post

    async def grab_notes(self, post):
        self.notes_for.append(post["id"])
        return None

    async def grab_tags(self, tags):
        return [{"name": name, "type": 0} for name in self.tags]


def fake_post(id=1, tags="cat"):
    return {
        "id": id,
        "md5": f"md5-{id}",
        "file_url": f"http://source.test/{id}.png",
        "tags": tags,
        "rating": "general",
        "source": "",
        "width": 100,
        "height": 100,
    }


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(main, "db", db)
    return db


def use_szurubooru(monkeypatch, handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(http, "szurubooru_session", client)


def scrape(monkeypatch, source, limit=10):
    monkeypatch.setitem(main.SOURCES, "fake", source)
    queue = asyncio.Queue()
    main.job_queues["job"] = queue
    asyncio.run(main.run_scrape("job", queue, limit, "fake", None, None, None))
    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())
    return messages


def test_rejected_upload_is_retried_next_run(monkeypatch, fake_db):
    responses = [httpx.Response(500, json={"name": "ServerError"}), httpx.Response(200, json={"id": 7})]
    use_szurubooru(monkeypatch, lambda request: responses.pop(0))

    first = scrape(monkeypatch, FakeSource([fake_post()]))
    second = scrape(monkeypatch, FakeSource([fake_post()]))

    assert first[-2]["failed"] == 1
    assert second[-2]["processed"] == 1
    assert fake_db.posts[("fake", 1)]["status"] == "processed"


def test_limit_counts_new_posts_not_known_ones(monkeypatch, fake_db):
    use_szurubooru(monkeypatch, lambda request: httpx.Response(200, json={"id": 7}))
    for id in (1, 2, 3):
        fake_db.posts[("fake", id)] = {"md5": f"md5-{id}", "status": "processed"}
    source = FakeSource([fake_post(id) for id in range(1, 11)])

    messages = scrape(monkeypatch, source, limit=2)

    assert messages[-2]["processed"] == 2
    assert messages[-2]["skipped"] == 3
    assert source.yielded == 5
    assert source.notes_for == [4, 5]


def test_failed_tag_sync_is_retried_and_does_not_stop_the_run(monkeypatch, fake_db):
    def handler(request):
        if "/tag" in request.url.path:
            return httpx.Response(500) if request.method == "POST" else httpx.Response(404)
        return httpx.Response(200, json={"id": 7})

    use_szurubooru(monkeypatch, handler)
    messages = scrape(monkeypatch, FakeSource([fake_post()], tags=["cat"]))

    assert fake_db.tags == set()
    assert messages[-2]["processed"] == 1


def test_tag_names_are_url_encoded(monkeypatch):
    from app import szurubooru

    paths = []

    def handler(request):
        paths.append(request.url.raw_path)
        return httpx.Response(404)

    use_szurubooru(monkeypatch, handler)
    asyncio.run(szurubooru.get_tag("what?#50%"))

    assert paths == [b"/api/tag/what%3F%2350%25"]


def test_empty_tags_are_not_sent(monkeypatch):
    from app import szurubooru

    bodies = []

    def handler(request):
        bodies.append(request.content)
        return httpx.Response(200, json={})

    use_szurubooru(monkeypatch, handler)
    asyncio.run(szurubooru.upload_post("http://x.test/1.png", " a  b ", "safe", 1, 1))

    assert b'"tags":["a","b"]' in bodies[0].replace(b" ", b"")


def test_search_failure_reaches_the_ui(monkeypatch, fake_db):
    class Broken(FakeSource):
        async def search_posts(self, **kwargs):
            raise RuntimeError("Gelbooru returned HTTP 429")
            yield

    messages = scrape(monkeypatch, Broken([]))

    assert any("HTTP 429" in m.get("log", "") for m in messages)
    assert messages[-1] == {"status": "error"}
    assert fake_db.runs[1] == "error"


@pytest.mark.parametrize(
    "body",
    [
        {"source": "danbooru", "limit": 1},
        {"source": "nope", "limit": 1},
        {"source": "gelbooru", "limit": 0},
    ],
)
def test_invalid_requests_are_refused(body):
    assert TestClient(main.app).post("/scrape", json=body).status_code in (400, 422)
