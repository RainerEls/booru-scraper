import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from helpers import mock_client

from app import integrity, szurubooru
from app.sources.base import BooruSource


def post(booru_id):
    return SimpleNamespace(
        id=booru_id,
        source="gelbooru", source_post_id=booru_id * 10, szurubooru_post_id=booru_id, image_url="http://src.test/a.png"
    )


def booru(monkeypatch, posts, deleted=(), merged=(), files=None, fail=None):
    def snapshots(ids):
        return [{"id": str(i), "user": {"name": "admin"}, "time": "2026-11-01"} for i in ids]

    def handler(request):
        path = request.url.path
        if fail and fail in path:
            return httpx.Response(403)
        if request.method == "HEAD":
            return (files or {}).get(path, httpx.Response(200, headers={"content-length": "100"}))
        if path.endswith("/snapshots"):
            results = snapshots(deleted if "deleted" in request.url.params["query"] else merged)
        else:
            results = [{"id": i, "contentUrl": f"data/posts/{i}.png", "fileSize": 100} for i in posts]
        offset = int(request.url.params["offset"])
        limit = int(request.url.params["limit"])
        return httpx.Response(200, json={"total": len(results), "results": results[offset : offset + limit]})

    monkeypatch.setattr(szurubooru, "session", mock_client(handler))


def report(rows):
    return asyncio.run(integrity.build_report(rows))


def kinds(result):
    return {issue["booru_post_id"]: issue["kind"] for issue in result["issues"]}


def test_removed_posts_are_told_apart_by_their_records(monkeypatch):
    booru(monkeypatch, posts=[1], deleted=[2], merged=[3])

    result = report([post(1), post(2), post(3), post(4)])

    assert kinds(result) == {2: "deleted", 3: "merged", 4: "missing"}
    assert result["counts"]["intact"] == 1
    deleted = next(i for i in result["issues"] if i["kind"] == "deleted")
    assert deleted["detail"] == "by admin, 2026-11-01"


def test_files_are_checked_by_presence_and_size(monkeypatch):
    files = {
        "/data/posts/2.png": httpx.Response(404),
        "/data/posts/3.png": httpx.Response(200, headers={"content-length": "5"}),
        "/data/posts/4.png": httpx.Response(502),
    }
    booru(monkeypatch, posts=[1, 2, 3, 4], files=files)

    assert kinds(report([post(i) for i in (1, 2, 3, 4)])) == {2: "damaged", 3: "damaged", 4: "unverified"}


def test_a_post_on_a_later_page_is_not_reported_missing(monkeypatch):
    ids = list(range(1, szurubooru.PAGE_SIZE + 6))
    booru(monkeypatch, posts=ids)

    assert report([post(ids[-1])])["issues"] == []


@pytest.mark.parametrize("path", ["/posts/", "/snapshots"])
def test_an_unreadable_booru_reports_nothing(monkeypatch, path):
    booru(monkeypatch, posts=[], fail=path)
    finished = {}

    class Db:
        async def get_uploaded_posts(self):
            return [post(1)]

        async def finish_check(self, check_id, status, report=None, error=None):
            finished.update(status=status, report=report, error=error)

    monkeypatch.setattr(integrity, "db", Db())
    asyncio.run(integrity.run_check(1))

    assert finished["status"] == "error"
    assert finished["report"] is None
    assert "HTTP 403" in finished["error"]


class RepullDb:
    def __init__(self, rows):
        self.rows = rows
        self.linked = {}
        self.finished = None

    async def get_posts(self, ids):
        return [row for row in self.rows if row.id in ids]

    async def finish_post(self, id, status, booru_id=None):
        self.linked[id] = booru_id

    async def finish_check(self, check_id, status, report=None, error=None):
        self.finished = (status, report, error)


class RepullSource(BooruSource):
    ratings = {"general": "safe"}

    def __init__(self, known=True):
        self.known = known

    async def search_posts(self, tags=""):
        if self.known:
            yield {
                "file_url": "http://src.test/new.png",
                "tags": "a b",
                "rating": "general",
                "width": 1,
                "height": 1,
                "source": "",
            }

    async def grab_tags(self, tags):
        return []

    async def grab_notes(self, post):
        return None


def issue(booru_id, kind):
    return {"kind": kind, "post_id": booru_id, "booru_post_id": booru_id, "source": "s", "source_post_id": booru_id * 10}


def repull_booru(monkeypatch, on_booru, deleted=(), writes=None):
    def handler(request):
        path = request.url.path
        if request.method == "HEAD":
            damaged = path.endswith("/2.png")
            return httpx.Response(404 if damaged else 200, headers={"content-length": "100"})
        if request.method == "POST" and path.endswith("/posts/"):
            writes.append(("upload", json.loads(request.content)))
            return httpx.Response(200, json={"id": 77})
        if request.method == "PUT":
            writes.append(("replace", path, json.loads(request.content)))
            return httpx.Response(200, json={})
        if "/post/" in path:
            return httpx.Response(200, json={"version": 5})
        if path.endswith("/snapshots"):
            ids = deleted if "deleted" in request.url.params["query"] else ()
            results = [{"id": str(i), "user": {"name": "admin"}, "time": "now"} for i in ids]
        else:
            results = [{"id": i, "contentUrl": f"data/posts/{i}.png", "fileSize": 100} for i in on_booru]
        return httpx.Response(200, json={"total": len(results), "results": results})

    monkeypatch.setattr(szurubooru, "session", mock_client(handler))
    monkeypatch.setattr(integrity, "REPULL_DELAY", 0)


def repull(monkeypatch, rows, issues, source=None):
    fake = RepullDb(rows)
    monkeypatch.setattr(integrity, "db", fake)
    report = {"counts": {}, "issues": issues}
    asyncio.run(integrity.run_repull(1, report, {"s": source or RepullSource()}))
    return fake


def source_rows(*ids):
    return [SimpleNamespace(**{**vars(post(i)), "source": "s", "source_post_id": i * 10}) for i in ids]


def test_missing_posts_upload_again_and_relink(monkeypatch):
    writes = []
    repull_booru(monkeypatch, on_booru=[], writes=writes)
    item = issue(1, "missing")

    fake = repull(monkeypatch, source_rows(1), [item])

    assert writes[0][0] == "upload"
    assert writes[0][1]["contentUrl"] == "http://src.test/new.png"
    assert writes[0][1]["safety"] == "safe"
    assert fake.linked == {1: 77}
    assert item["repull"] == "Uploaded again"


def test_damaged_files_are_replaced_on_the_same_post(monkeypatch):
    writes = []
    repull_booru(monkeypatch, on_booru=[2], writes=writes)
    item = issue(2, "damaged")

    fake = repull(monkeypatch, source_rows(2), [item])

    assert writes == [("replace", "/api/post/2", {"version": 5, "contentUrl": "http://src.test/new.png"})]
    assert fake.linked == {}
    assert item["repull"] == "File replaced"


def test_a_post_deleted_since_the_check_is_left_alone(monkeypatch):
    writes = []
    repull_booru(monkeypatch, on_booru=[], deleted=[1], writes=writes)
    item = issue(1, "missing")

    repull(monkeypatch, source_rows(1), [item])

    assert writes == []
    assert item["repull"] == "Left alone, as it's now deleted"


def test_deleted_and_merged_posts_are_never_pulled(monkeypatch):
    writes = []
    repull_booru(monkeypatch, on_booru=[], writes=writes)
    items = [issue(1, "deleted"), issue(2, "merged")]

    repull(monkeypatch, source_rows(1, 2), items)

    assert writes == []
    assert all("repull" not in item for item in items)


def test_a_post_the_source_lost_is_reported(monkeypatch):
    writes = []
    repull_booru(monkeypatch, on_booru=[], writes=writes)
    item = issue(1, "missing")

    repull(monkeypatch, source_rows(1), [item], RepullSource(known=False))

    assert writes == []
    assert item["repull"] == "The source no longer has this post"


def test_one_failure_does_not_stop_the_rest(monkeypatch):
    writes = []
    repull_booru(monkeypatch, on_booru=[], writes=writes)
    items = [issue(1, "missing"), issue(3, "missing")]

    class Flaky(RepullSource):
        calls = 0

        async def search_posts(self, tags=""):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise RuntimeError("HTTP 500")
            async for post in super().search_posts(tags):
                yield post

    fake = repull(monkeypatch, source_rows(1, 3), items, Flaky())

    assert items[0]["repull"].startswith("Failed")
    assert items[1]["repull"] == "Uploaded again"
    assert fake.finished[0] == "done"


def test_an_unreadable_booru_pulls_nothing(monkeypatch):
    writes = []
    repull_booru(monkeypatch, on_booru=[], writes=writes)

    def refused(request):
        return httpx.Response(403)

    monkeypatch.setattr(szurubooru, "session", mock_client(refused))
    item = issue(1, "missing")

    fake = repull(monkeypatch, source_rows(1), [item])

    assert writes == []
    assert "repull" not in item
    assert "Nothing was re-pulled" in fake.finished[2]
