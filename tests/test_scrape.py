import asyncio
import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx2
import pytest
from fastapi.testclient import TestClient
from helpers import mock_client

from app import main, scrape, szurubooru
from app.sources.base import BooruSource


class FakeDb:
    def __init__(self):
        self.posts = {}
        self.tags = set()
        self.runs = {}

    async def create_run(self, **fields):
        self.runs[1] = "running"
        return 1

    async def update_run(self, id, status, log, failed_posts, **counters):
        self.runs[id] = status
        self.saved = {"log": log, "failed_posts": failed_posts}

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

    async def booru_post_for_md5(self, md5):
        ids = [p.get("szurubooru_post_id") for p in self.posts.values() if p["md5"] == md5]
        return next(filter(None, ids), None)

    async def count_tags(self):
        return len(self.tags)

    async def tag_exists(self, tag_name):
        return tag_name in self.tags

    async def store_tag(self, tag_name):
        self.tags.add(tag_name)

    async def finish_post(self, id, status, szurubooru_post_id=None):
        self.posts[id]["status"] = status


class FakeSource(BooruSource):
    def __init__(self, posts, tags=()):
        self.posts = posts
        self.tags = tags
        self.yielded = 0
        self.notes_for = []

    async def search_posts(self, tags="", blacklist_tags="", rating=None):
        self.blacklist = blacklist_tags
        for post in self.posts:
            self.yielded += 1
            yield post

    async def grab_notes(self, post):
        self.notes_for.append(post["id"])
        return None

    async def grab_tags(self, tags):
        for name in self.tags:
            yield {"name": name, "category": "general"}


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
    async def categories_exist(names):
        pass

    db = FakeDb()
    async def connected():
        return None

    monkeypatch.setattr(szurubooru, "check_connection", connected)
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(scrape, "db", db)
    monkeypatch.setattr(scrape.szurubooru, "ensure_tag_categories", categories_exist)
    return db


def use_szurubooru(monkeypatch, handler):
    monkeypatch.setattr(szurubooru, "session", mock_client(handler))


def run_job():
    job = scrape.Job()
    scrape.jobs["job"] = job
    return job


def run_fake_scrape(monkeypatch, source, limit=10):
    monkeypatch.setitem(scrape.SOURCES, "fake", source)
    job = run_job()
    asyncio.run(scrape.run_scrape("job", job, scrape.ScrapeSpec("fake", limit)))
    return job.events


def test_rejected_upload_is_retried_next_run(monkeypatch, fake_db):
    responses = [httpx2.Response(500, json={"name": "ServerError"}), httpx2.Response(200, json={"id": 7})]
    use_szurubooru(monkeypatch, lambda request: responses.pop(0))

    first = run_fake_scrape(monkeypatch, FakeSource([fake_post()]))
    second = run_fake_scrape(monkeypatch, FakeSource([fake_post()]))

    assert first[-2]["failed"] == 1
    assert second[-2]["processed"] == 1
    assert fake_db.posts[("fake", 1)]["status"] == "processed"


def test_a_post_szurubooru_can_never_take_stops_blocking_its_search(monkeypatch, fake_db):
    uploaded = []

    def handler(request):
        url = json.loads(request.content)["contentUrl"]
        if url.endswith("/1.png"):
            return httpx2.Response(400, json={"name": "DownloadTooLargeError"})
        uploaded.append(url)
        return httpx2.Response(200, json={"id": 7})

    use_szurubooru(monkeypatch, handler)
    posts = [fake_post(1), fake_post(2)]

    first = run_fake_scrape(monkeypatch, FakeSource(posts), limit=1)
    second = run_fake_scrape(monkeypatch, FakeSource(posts), limit=1)

    assert first[-2]["failed"] == 1
    assert fake_db.posts[("fake", 1)]["status"] == "rejected"
    assert second[-2]["processed"] == 1
    assert uploaded == ["http://source.test/2.png"]


def test_the_shared_blacklist_applies_to_every_scrape(monkeypatch, fake_db):
    monkeypatch.setattr(scrape.config, "BLACKLIST_TAGS", "gore dog")
    source = FakeSource([])
    monkeypatch.setitem(scrape.SOURCES, "fake", source)

    asyncio.run(scrape.run_scrape("job", run_job(), scrape.ScrapeSpec("fake", 1, blacklist_tags="dog cat")))

    assert source.blacklist == "gore dog cat"


def test_progress_reports_each_phase(monkeypatch, fake_db):
    def handler(request):
        if request.method == "GET":
            return httpx2.Response(404)
        return httpx2.Response(200, json={"id": 7})

    use_szurubooru(monkeypatch, handler)

    messages = run_fake_scrape(monkeypatch, FakeSource([fake_post()], tags=["cat"]), limit=5)

    last = {m["phase"]: (m["done"], m["goal"]) for m in messages if "phase" in m}
    assert last["Searching"] == (1, 5)
    assert last["Syncing tags"] == (1, 1)
    assert last["Uploading"] == (1, 1)


def test_uploads_link_back_to_the_post(monkeypatch):
    class Linked(FakeSource):
        post_page = "http://source.test/posts/{id}"

    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json={"id": 7})

    use_szurubooru(monkeypatch, handler)
    source = Linked([])
    post = {**fake_post(5), "source": "http://artist.test/"}

    asyncio.run(szurubooru.upload_post(post, "safe", source.source_links(post)))
    assert bodies[0]["source"] == "http://source.test/posts/5\nhttp://artist.test/"
    assert len(source.source_links({**post, "source": "x" * 5000})) == 2048


def test_a_source_without_its_keys_is_refused(monkeypatch):
    class NeedsKeys(FakeSource):
        credentials = (None,)

    monkeypatch.setitem(scrape.SOURCES, "gelbooru", NeedsKeys([]))
    client = TestClient(main.app)

    assert client.post("/scrape", json={"source": "gelbooru", "limit": 1}).status_code == 422
    assert next(s for s in client.get("/api/sources").json() if s["name"] == "gelbooru")["configured"] is False


def test_stopping_keeps_synced_tags_and_releases_claims(monkeypatch, fake_db):
    class Slow(FakeSource):
        async def grab_tags(self, tags):
            yield {"name": "synced", "category": "general"}
            await asyncio.sleep(60)
            yield {"name": "never", "category": "general"}

    def handler(request):
        return httpx2.Response(404 if request.method == "GET" else 200, json={})

    use_szurubooru(monkeypatch, handler)
    monkeypatch.setitem(scrape.SOURCES, "fake", Slow([fake_post()]))

    async def run():
        job = scrape.Job()
        job.task = asyncio.create_task(scrape.run_scrape("job", job, scrape.ScrapeSpec("fake", 5)))
        while "synced" not in fake_db.tags:
            await asyncio.sleep(0)
        job.stop()
        job.stop()
        await job.task
        return job

    job = asyncio.run(run())

    assert job.finished
    assert job.events[-1] == {"status": "stopped"}
    assert fake_db.runs[1] == "stopped"
    assert fake_db.tags == {"synced"}
    assert ("fake", 1) not in fake_db.posts
    assert fake_db.saved["log"][-1] == {"level": "warning", "message": "Stopped"}


def test_a_run_keeps_its_log_and_failed_posts(monkeypatch, fake_db):
    use_szurubooru(monkeypatch, lambda request: httpx2.Response(500, json={"name": "ServerError"}))

    run_fake_scrape(monkeypatch, FakeSource([fake_post(3)]))

    assert fake_db.saved["failed_posts"] == [3]
    assert any("ServerError" in line["message"] for line in fake_db.saved["log"])


def test_retrying_looks_posts_up_by_id(monkeypatch, fake_db):
    class ById(FakeSource):
        async def search_posts(self, tags="", blacklist_tags="", rating=None):
            yield fake_post(int(tags.removeprefix("id:")))

    monkeypatch.setattr("app.sources.base.ID_LOOKUP_DELAY", 0)
    use_szurubooru(monkeypatch, lambda request: httpx2.Response(200, json={"id": 7}))
    monkeypatch.setitem(scrape.SOURCES, "fake", ById([]))
    job = run_job()

    asyncio.run(scrape.run_scrape("job", job, scrape.ScrapeSpec("fake", 2, post_ids=[4, 9])))

    assert job.events[-2]["processed"] == 2
    assert set(fake_db.posts) == {("fake", 4), ("fake", 9)}


def test_retry_route_clears_rejections_and_starts_a_job(monkeypatch, fake_db):
    released = []
    started = []

    async def get_run(id):
        return SimpleNamespace(source="gelbooru", failed_posts=[4, 9]) if id == 1 else None

    async def release_rejected(source, ids):
        released.append((source, ids))

    monkeypatch.setattr(fake_db, "get_run", get_run, raising=False)
    monkeypatch.setattr(fake_db, "release_rejected", release_rejected, raising=False)
    monkeypatch.setattr(main, "start_job", lambda spec: started.append(spec) or "job")
    client = TestClient(main.app)

    assert client.post("/api/runs/2/retry").status_code == 404
    assert client.post("/api/runs/1/retry").json() == {"job_id": "job"}
    assert released == [("gelbooru", [4, 9])]
    assert started[0].post_ids == [4, 9]


def test_a_scheduled_run_stops_once_it_is_caught_up(monkeypatch, fake_db):
    for id in range(1, 4):
        fake_db.posts[("fake", id)] = {"md5": f"md5-{id}", "status": "processed"}
    use_szurubooru(monkeypatch, lambda request: httpx2.Response(200, json={"id": 7}))
    source = FakeSource([fake_post(9)] + [fake_post(id) for id in range(1, 4)] + [fake_post(10)])
    monkeypatch.setitem(scrape.SOURCES, "fake", source)
    job = run_job()

    asyncio.run(scrape.run_scrape("job", job, scrape.ScrapeSpec("fake", 10, stop_after_known=2)))

    assert source.yielded == 3
    assert ("fake", 9) in fake_db.posts
    assert ("fake", 10) not in fake_db.posts


def schedule(**fields):
    now = scrape.utcnow()
    defaults = dict(
        id=1, source="gelbooru", tags="cat", blacklist_tags="", rating=None, post_limit=5,
        interval_hours=24, enabled=True, last_run_at=now - timedelta(hours=25),
    )
    return SimpleNamespace(**{**defaults, **fields})


def schedules_with(monkeypatch, fake_db, *rows):
    started = []
    marked = []

    async def get_schedules():
        return list(rows)

    async def mark_schedule_started(id):
        marked.append(id)

    monkeypatch.setattr(fake_db, "get_schedules", get_schedules, raising=False)
    monkeypatch.setattr(fake_db, "mark_schedule_started", mark_schedule_started, raising=False)
    monkeypatch.setattr(scrape, "start_job", lambda spec: started.append(spec) or "job")
    return started, marked


@pytest.mark.parametrize(
    "fields, due",
    [
        ({}, True),
        ({"last_run_at": None}, True),
        ({"last_run_at": scrape.utcnow() - timedelta(hours=1)}, False),
        ({"enabled": False}, False),
        ({"source": "removed"}, False),
    ],
)
def test_only_due_schedules_start(monkeypatch, fake_db, fields, due):
    started, marked = schedules_with(monkeypatch, fake_db, schedule(**fields))

    asyncio.run(scrape.start_due_schedule())

    assert bool(started) is due
    assert bool(marked) is due


def test_schedules_wait_for_a_running_scrape_and_run_one_at_a_time(monkeypatch, fake_db):
    started, _ = schedules_with(monkeypatch, fake_db, schedule(id=1), schedule(id=2))

    job = run_job()
    asyncio.run(scrape.start_due_schedule())
    assert started == []

    job.finished = True
    asyncio.run(scrape.start_due_schedule())
    assert [spec.schedule_id for spec in started] == [1]
    assert started[0].stop_after_known == scrape.SCHEDULED_STOP_AFTER_KNOWN


def test_a_schedule_without_an_interval_is_refused():
    response = TestClient(main.app).post("/api/schedules", json={"source": "gelbooru", "limit": 1})
    assert response.status_code == 422


def test_limit_counts_new_posts_not_known_ones(monkeypatch, fake_db):
    use_szurubooru(monkeypatch, lambda request: httpx2.Response(200, json={"id": 7}))
    for id in (1, 2, 3):
        fake_db.posts[("fake", id)] = {"md5": f"md5-{id}", "status": "processed"}
    source = FakeSource([fake_post(id) for id in range(1, 11)])

    messages = run_fake_scrape(monkeypatch, source, limit=2)

    assert messages[-2]["processed"] == 2
    assert messages[-2]["skipped"] == 3
    assert source.yielded == 5
    assert source.notes_for == [4, 5]


def test_failed_tag_sync_is_retried_and_does_not_stop_the_run(monkeypatch, fake_db):
    def handler(request):
        if "/tag" in request.url.path:
            return httpx2.Response(500) if request.method == "POST" else httpx2.Response(404)
        return httpx2.Response(200, json={"id": 7})

    use_szurubooru(monkeypatch, handler)
    messages = run_fake_scrape(monkeypatch, FakeSource([fake_post()], tags=["cat"]))

    assert fake_db.tags == set()
    assert messages[-2]["processed"] == 1


def test_tag_names_are_url_encoded(monkeypatch):
    paths = []

    def handler(request):
        paths.append(request.url.raw_path)
        return httpx2.Response(404)

    use_szurubooru(monkeypatch, handler)
    asyncio.run(szurubooru.get_tag("what?#50%"))

    assert paths == [b"/api/tag/what%3F%2350%25"]


def test_updating_a_tag_keeps_its_other_names(monkeypatch):
    bodies = []

    def handler(request):
        if request.method == "GET":
            return httpx2.Response(200, json={"names": ["cat", "neko"], "version": 3})
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json={})

    use_szurubooru(monkeypatch, handler)
    asyncio.run(szurubooru.sync_tag("neko", "general"))

    assert bodies == [{"category": "general", "version": 3}]


def test_a_database_failure_after_upload_counts_once(monkeypatch, fake_db):
    async def broken(id, status, szurubooru_post_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(fake_db, "finish_post", broken)
    use_szurubooru(monkeypatch, lambda request: httpx2.Response(200, json={"id": 7}))

    messages = run_fake_scrape(monkeypatch, FakeSource([fake_post()]))

    assert (messages[-2]["processed"], messages[-2]["failed"]) == (0, 1)


def test_empty_tags_are_not_sent(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(request.content)
        return httpx2.Response(200, json={})

    use_szurubooru(monkeypatch, handler)
    asyncio.run(szurubooru.upload_post({"file_url": "http://x.test/1.png", "tags": " a  b "}, "safe"))

    assert b'"tags":["a","b"]' in bodies[0].replace(b" ", b"")


def warnings(messages):
    return [m["log"] for m in messages if m.get("level") == "warning"]


def test_slow_sources_warn_while_few_tags_are_known(monkeypatch, fake_db):
    class Slow(FakeSource):
        slow_tag_lookup = True

    assert "fresh instance" in warnings(run_fake_scrape(monkeypatch, Slow([])))[0]

    fake_db.tags = {str(i) for i in range(scrape.FEW_KNOWN_TAGS)}
    assert warnings(run_fake_scrape(monkeypatch, Slow([]))) == []
    assert warnings(run_fake_scrape(monkeypatch, FakeSource([]))) == []


def test_search_failure_still_uploads_what_was_found(monkeypatch, fake_db):
    class FailsPartWay(FakeSource):
        async def search_posts(self, **kwargs):
            yield fake_post(1)
            raise RuntimeError("Gelbooru returned HTTP 429")

    use_szurubooru(monkeypatch, lambda request: httpx2.Response(200, json={"id": 7}))
    messages = run_fake_scrape(monkeypatch, FailsPartWay([]))

    assert fake_db.posts[("fake", 1)]["status"] == "processed"
    assert messages[-1] == {"status": "error"}
    assert fake_db.runs[1] == "error"


def test_search_failure_reaches_the_ui(monkeypatch, fake_db):
    class Broken(FakeSource):
        async def search_posts(self, **kwargs):
            raise RuntimeError("Gelbooru returned HTTP 429")
            yield

    messages = run_fake_scrape(monkeypatch, Broken([]))

    assert any("HTTP 429" in m.get("log", "") for m in messages)
    assert messages[-1] == {"status": "error"}
    assert fake_db.runs[1] == "error"


@pytest.mark.parametrize(
    "body",
    [
        {"source": "nope", "limit": 1},
        {"source": "gelbooru", "limit": 0},
    ],
)
def test_invalid_requests_are_refused(body):
    assert TestClient(main.app).post("/scrape", json=body).status_code == 422


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
        job = scrape.Job()
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


def test_only_missing_tag_categories_are_created(monkeypatch):
    created = []

    def handler(request):
        if request.method == "POST":
            created.append(json.loads(request.content))
            return httpx2.Response(200, json={})
        return httpx2.Response(200, json={"results": [{"name": "default"}, {"name": "general"}]})

    use_szurubooru(monkeypatch, handler)
    asyncio.run(szurubooru.ensure_tag_categories(["general", "species", "species", "artist"]))

    assert [(c["name"], c["order"]) for c in created] == [("artist", 3), ("species", 4)]


def test_a_category_failure_does_not_stop_the_run(monkeypatch, fake_db):
    async def refused(names):
        raise httpx2.ConnectError("refused")

    monkeypatch.setattr(scrape.szurubooru, "ensure_tag_categories", refused)
    messages = run_fake_scrape(monkeypatch, FakeSource([]))

    assert "tag categories" in warnings(messages)[0]
    assert messages[-1] == {"status": "done"}


def test_local_time_follows_tz(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    try:
        assert main.local_time(datetime(2026, 11, 1, 20, 30)) == "2026-11-02 05:30"
    finally:
        monkeypatch.undo()
        time.tzset()


def test_a_restart_marks_the_run_interrupted_and_keeps_its_log(monkeypatch, fake_db):
    class SlowSource(FakeSource):
        async def search_posts(self, **kwargs):
            await asyncio.Event().wait()
            yield

    use_szurubooru(monkeypatch, lambda request: httpx2.Response(200, json={"results": []}))
    monkeypatch.setitem(scrape.SOURCES, "fake", SlowSource([]))

    async def scenario():
        job = scrape.Job()
        job.task = asyncio.create_task(scrape.run_scrape("run", job, scrape.ScrapeSpec(source="fake", limit=1)))
        await asyncio.sleep(0.01)
        job.stop("interrupted")
        await job.task
        return job

    job = asyncio.run(scenario())
    assert fake_db.runs[1] == "interrupted"
    assert job.events[-1] == {"status": "interrupted"}
    assert any(line["message"] == "Interrupted" for line in job.lines)


def test_health_reports_whether_the_database_answers(monkeypatch):
    async def ping():
        return None

    async def no_database():
        raise OSError("connection refused")

    client = TestClient(main.app)
    monkeypatch.setattr(scrape.db, "ping", ping)
    assert client.get("/health").status_code == 200
    monkeypatch.setattr(scrape.db, "ping", no_database)
    assert client.get("/health").status_code == 503


def merge_booru(monkeypatch, current=None, status=200):
    current = current or {"version": 3, "tags": [{"names": ["cat", "feline"]}], "source": "http://first.test/1"}
    sent = []

    def handler(request):
        if request.method == "PUT" and "/post/" in str(request.url):
            sent.append((str(request.url), json.loads(request.content)))
        if "/post/" in str(request.url):
            return httpx2.Response(status, json=current)
        if request.method == "POST" and str(request.url).endswith("/posts/"):
            return httpx2.Response(200, json={"id": 50})
        return httpx2.Response(404 if request.method == "GET" else 200, json={"results": [], "version": 1})

    use_szurubooru(monkeypatch, handler)
    return sent


def duplicate_scrape(monkeypatch, fake_db, tags="cat dog", original=7):
    fake_db.posts[("other", 9)] = {"md5": "md5-1", "status": "processed", "szurubooru_post_id": original}
    source = FakeSource([fake_post(tags=tags)], tags.split())
    source.post_page = "http://fake.test/{id}"
    monkeypatch.setitem(scrape.SOURCES, "fake", source)
    job = scrape.Job()
    asyncio.run(scrape.run_scrape("job", job, scrape.ScrapeSpec("fake", 5)))
    return job


def test_a_duplicate_adds_its_tags_and_link_to_the_uploaded_post(monkeypatch, fake_db):
    sent = merge_booru(monkeypatch)

    job = duplicate_scrape(monkeypatch, fake_db)

    assert sent == [
        (
            "http://szurubooru.test/api/post/7",
            {"version": 3, "tags": ["cat", "dog"], "source": "http://first.test/1\nhttp://fake.test/1"},
        )
    ]
    assert fake_db.posts[("fake", 1)]["status"] == "merged"
    assert job.counters["skipped"] == 1
    assert "dog" in fake_db.tags


def test_a_duplicate_with_nothing_new_writes_nothing(monkeypatch, fake_db):
    current = {"version": 3, "tags": [{"names": ["cat"]}], "source": "http://fake.test/1"}
    sent = merge_booru(monkeypatch, current)

    duplicate_scrape(monkeypatch, fake_db, tags="cat")

    assert sent == []
    assert fake_db.posts[("fake", 1)]["status"] == "merged"


def test_a_failed_merge_is_tried_again_next_run(monkeypatch, fake_db):
    merge_booru(monkeypatch, status=500)

    job = duplicate_scrape(monkeypatch, fake_db)

    assert ("fake", 1) not in fake_db.posts
    assert job.status == "done"
    assert any("retried next run" in line["message"] for line in job.lines)


def test_a_duplicate_with_no_booru_post_on_record_is_uploaded(monkeypatch, fake_db):
    sent = merge_booru(monkeypatch)

    job = duplicate_scrape(monkeypatch, fake_db, original=None)

    assert sent == []
    assert fake_db.posts[("fake", 1)]["status"] == "processed"
    assert job.counters["processed"] == 1
    assert job.counters["skipped"] == 0


def test_a_post_szurubooru_already_has_is_merged_into_that_post(monkeypatch, fake_db):
    current = {"version": 3, "tags": [{"names": ["cat"]}], "source": None}
    sent = []

    def handler(request):
        if str(request.url).endswith("/posts/"):
            return httpx2.Response(400, json={"name": "PostAlreadyUploadedError", "otherPostId": 12})
        if "/post/12" in str(request.url):
            if request.method == "PUT":
                sent.append(json.loads(request.content))
            return httpx2.Response(200, json=current)
        return httpx2.Response(404 if request.method == "GET" else 200, json={"results": []})

    use_szurubooru(monkeypatch, handler)
    source = FakeSource([fake_post(tags="cat dog")], ["cat", "dog"])
    source.post_page = "http://fake.test/{id}"
    monkeypatch.setitem(scrape.SOURCES, "fake", source)
    job = scrape.Job()

    asyncio.run(scrape.run_scrape("job", job, scrape.ScrapeSpec("fake", 5)))

    assert sent == [{"version": 3, "tags": ["cat", "dog"], "source": "http://fake.test/1"}]
    assert fake_db.posts[("fake", 1)]["status"] == "merged"
    assert job.counters["skipped"] == 1


def test_run_again_repeats_the_same_search(monkeypatch):
    run = SimpleNamespace(
        source="gelbooru",
        tags="cat",
        blacklist_tags="dog",
        rating="safe",
        post_limit=20,
        retry_of=None,
    )
    specs = []

    async def get_run(id):
        return run if id == 1 else None

    monkeypatch.setattr(scrape.db, "get_run", get_run)
    monkeypatch.setattr(main, "start_job", lambda spec: specs.append(spec) or "job")
    monkeypatch.setattr(scrape.SOURCES["gelbooru"], "credentials", ("x",), raising=False)
    client = TestClient(main.app)

    assert client.post("/api/runs/2/again").status_code == 404
    assert client.post("/api/runs/1/again").json() == {"job_id": "job"}
    assert specs == [scrape.ScrapeSpec("gelbooru", 20, "cat", "dog", "safe")]

    run.retry_of = 7
    assert client.post("/api/runs/1/again").status_code == 404
