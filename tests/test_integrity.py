import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app import http, integrity


def post(booru_id):
    return SimpleNamespace(
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

    monkeypatch.setattr(http, "szurubooru_session", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


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
    ids = list(range(1, integrity.PAGE_SIZE + 6))
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
