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


def run_job():
    job = main.Job()
    main.jobs["job"] = job
    return job


def scrape(monkeypatch, source, limit=10):
    monkeypatch.setitem(main.SOURCES, "fake", source)
    job = run_job()
    asyncio.run(main.run_scrape("job", job, limit, "fake", None, None, None))
    return job.events


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


def test_a_reconnecting_client_replays_the_whole_run():
    job = run_job()
    job.publish({"processed": 1, "log": "first"})
    job.finished = True
    job.publish({"status": "done"})

    with TestClient(main.app).stream("GET", "/scrape/progress/job") as response:
        lines = [line for line in response.iter_lines() if line.startswith("data")]

    assert len(lines) == 2
    assert "first" in lines[0]
    assert "done" in lines[1]


def test_a_client_receives_events_published_after_it_connects():
    async def run():
        job = main.Job()
        events = []

        async def listen():
            async for event in main.scrape_progress(job):
                events.append(event)

        listener = asyncio.create_task(listen())
        await asyncio.sleep(0)
        job.publish({"log": "later"})
        await asyncio.sleep(0)
        job.publish({"status": "done"})
        await asyncio.wait_for(listener, 1)
        return events

    assert asyncio.run(run()) == [{"log": "later"}, {"status": "done"}]


def test_unknown_jobs_are_not_found():
    assert TestClient(main.app).get("/scrape/progress/missing").status_code == 404


def test_only_unfinished_jobs_are_active():
    job = run_job()
    client = TestClient(main.app)
    assert "job" in client.get("/api/scrapes/active").json()
    job.finished = True
    assert "job" not in client.get("/api/scrapes/active").json()
