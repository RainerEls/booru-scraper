import asyncio
import xml.etree.ElementTree as ET

import httpx
import pytest
from fastapi.testclient import TestClient
from helpers import mock_client
from test_scrape import FakeDb, FakeSource, fake_post

from app import config, http, integrity, main, scrape, szurubooru
from app.http import ApiError, CredentialsRejected, SetupProblem, api_get, describe


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(scrape, "db", db)
    return db


def run_job(monkeypatch, source, spec=None):
    monkeypatch.setitem(scrape.SOURCES, "fake", source)
    job = scrape.Job()
    asyncio.run(scrape.run_scrape("job", job, spec or scrape.ScrapeSpec("fake", 10)))
    return job


def booru(monkeypatch, handler):
    monkeypatch.setattr(szurubooru, "session", mock_client(handler))


def categories_ok(request):
    return httpx.Response(200, json={"results": []})


def error_lines(job):
    return [line["message"] for line in job.lines if line["level"] == "error"]


@pytest.mark.parametrize(
    "error, text",
    [
        (ApiError("http://source.test/posts", 429), "source.test is rate limiting requests"),
        (ApiError("http://source.test/posts", 503), "source.test is having problems (HTTP 503)"),
        (ApiError("http://source.test/posts", 404), "source.test returned HTTP 404"),
        (httpx.ReadTimeout("slow"), "The request timed out"),
        (httpx.ConnectError("refused"), "The connection failed"),
        (ValueError("not json"), "The response wasn't in the expected format"),
        (KeyError("posts"), "The response wasn't in the expected format"),
        (ET.ParseError("bad xml"), "The response wasn't in the expected format"),
        (RuntimeError("Danbooru allows 2 tags"), "Danbooru allows 2 tags"),
    ],
)
def test_errors_read_as_plain_language(error, text):
    assert describe(error) == text


def test_a_status_error_from_szurubooru_reads_the_same_way():
    request = httpx.Request("PUT", "http://booru.test/api/post/1")
    error = httpx.HTTPStatusError("long message", request=request, response=httpx.Response(500, request=request))

    assert describe(error) == "booru.test is having problems (HTTP 500)"


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_request_never_shows_the_key(status):
    client = mock_client(lambda request: httpx.Response(status))

    with pytest.raises(CredentialsRejected) as error:
        asyncio.run(api_get(client, "http://source.test/posts", {"api_key": "secret-key"}))

    assert "source.test refused" in str(error.value)
    assert "secret-key" not in str(error.value)


def test_placeholder_values_count_as_unset(monkeypatch):
    monkeypatch.setenv("SOME_KEY", "SOME_KEY")
    assert config._setting("SOME_KEY") == ""
    monkeypatch.setenv("SOME_KEY", "real")
    assert config._setting("SOME_KEY") == "real"


def test_missing_szurubooru_settings_are_named(monkeypatch):
    monkeypatch.setitem(config._SZURUBOORU_SETTINGS, "SZURUBOORU_API_TOKEN", "")

    with pytest.raises(SetupProblem, match="Set SZURUBOORU_API_TOKEN in .env"):
        asyncio.run(szurubooru.check_connection())


@pytest.mark.parametrize(
    "response, problem",
    [
        (httpx.Response(403, json={"name": "AuthError"}), CredentialsRejected),
        (httpx.Response(404, json={"name": "UserNotFoundError"}), SetupProblem),
        (httpx.Response(200, text="<!DOCTYPE html>"), SetupProblem),
        (httpx.Response(200, json=["not the api"]), SetupProblem),
    ],
)
def test_a_bad_szurubooru_setup_is_a_setup_problem(monkeypatch, response, problem):
    booru(monkeypatch, lambda request: response)

    with pytest.raises(problem):
        asyncio.run(szurubooru.check_connection())


def test_an_unreachable_szurubooru_names_its_url(monkeypatch):
    def down(request):
        raise httpx.ConnectError("refused")

    booru(monkeypatch, down)

    with pytest.raises(SetupProblem, match="Could not reach szurubooru at http://szurubooru.test/api"):
        asyncio.run(szurubooru.check_connection())


def test_a_good_szurubooru_setup_passes(monkeypatch):
    booru(monkeypatch, categories_ok)

    asyncio.run(szurubooru.check_connection())


def test_a_source_without_its_key_stops_before_searching(monkeypatch, fake_db):
    class NoKeys(FakeSource):
        credentials = ("",)

        async def search_posts(self, **kwargs):
            raise AssertionError("searched without a key")
            yield

    booru(monkeypatch, categories_ok)

    job = run_job(monkeypatch, NoKeys([]))

    assert job.status == "error"
    assert fake_db.runs[1] == "error"
    assert error_lines(job) == ["Stopped: fake needs its API key and user id set in .env."]


def test_a_bad_szurubooru_token_stops_before_searching(monkeypatch, fake_db):
    source = FakeSource([fake_post()])
    booru(monkeypatch, lambda request: httpx.Response(403, json={"name": "AuthError"}))

    job = run_job(monkeypatch, source)

    assert source.yielded == 0
    assert job.status == "error"
    assert "szurubooru.test refused the request (HTTP 403)" in error_lines(job)[0]


def test_a_key_rejected_during_the_search_stops_the_run(monkeypatch, fake_db):
    class Rejected(FakeSource):
        async def search_posts(self, **kwargs):
            yield fake_post(1)
            raise CredentialsRejected("http://source.test/posts", 401)

    uploads = []

    def handler(request):
        if request.method == "POST":
            uploads.append(request)
        return categories_ok(request)

    booru(monkeypatch, handler)

    job = run_job(monkeypatch, Rejected([]))

    assert uploads == []
    assert ("fake", 1) not in fake_db.posts
    assert job.status == "error"
    assert error_lines(job) == [
        "Stopped: source.test refused the request (HTTP 401). Check the credentials for it in .env."
    ]


def test_a_rejected_upload_cancels_the_rest_of_the_run(monkeypatch, fake_db):
    monkeypatch.setattr(config, "CONCURRENT_UPLOADS", 1)
    uploads = []

    def handler(request):
        if str(request.url).endswith("/posts/"):
            uploads.append(request)
            return httpx.Response(403, json={"name": "AuthError"})
        return httpx.Response(200, json={"results": [], "version": 1})

    booru(monkeypatch, handler)
    source = FakeSource([fake_post(id) for id in range(1, 6)])

    job = run_job(monkeypatch, source)

    assert len(uploads) == 1
    assert job.status == "error"
    assert job.counters["failed"] == 0
    assert not fake_db.posts
    assert job.finished


def test_a_search_error_reads_plainly_and_keeps_the_posts_found(monkeypatch, fake_db):
    class FailsLater(FakeSource):
        async def search_posts(self, **kwargs):
            yield fake_post(1)
            raise ApiError("http://source.test/posts", 503)

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": 7})
        return httpx.Response(200, json={"results": [], "version": 1})

    booru(monkeypatch, handler)

    job = run_job(monkeypatch, FailsLater([]))

    assert job.counters["processed"] == 1
    assert job.status == "error"
    assert error_lines(job) == [
        "Search failed, continuing with the 1 new post(s) found: source.test is having problems (HTTP 503)"
    ]


def test_a_schedule_for_a_source_without_keys_records_why_it_did_not_run(monkeypatch, fake_db):
    class NoKeys(FakeSource):
        credentials = ("",)

    schedule = main.db.Schedules(
        id=1, source="fake", tags="", blacklist_tags="", post_limit=1, interval_hours=1, enabled=True, last_run_at=None
    )
    started = []

    async def get_schedules():
        return [schedule]

    async def mark_schedule_started(id):
        return None

    monkeypatch.setitem(scrape.SOURCES, "fake", NoKeys([]))
    monkeypatch.setattr(fake_db, "get_schedules", get_schedules, raising=False)
    monkeypatch.setattr(fake_db, "mark_schedule_started", mark_schedule_started, raising=False)
    monkeypatch.setattr(scrape, "start_job", lambda spec: started.append(spec) or "job")
    monkeypatch.setattr(scrape, "jobs", {})

    asyncio.run(scrape.start_due_schedule())

    assert started[0].source == "fake"


def test_integrity_routes_need_szurubooru(monkeypatch):
    monkeypatch.setitem(config._SZURUBOORU_SETTINGS, "SZURUBOORU_BASE_URL", "")
    client = TestClient(main.app)

    response = client.post("/api/integrity-checks")

    assert response.status_code == 422
    assert "SZURUBOORU_BASE_URL" in response.json()["detail"]
    assert client.post("/api/integrity-checks/1/repull").status_code == 422


def test_a_rejected_integrity_check_explains_itself(monkeypatch):
    finished = {}

    async def finish_check(id, status, report=None, error=None):
        finished.update(status=status, error=error)

    async def get_uploaded_posts():
        return []

    async def send(event, body):
        return None

    monkeypatch.setattr(integrity.db, "finish_check", finish_check)
    monkeypatch.setattr(integrity.db, "get_uploaded_posts", get_uploaded_posts)
    monkeypatch.setattr(integrity.notify, "send", send)
    booru(monkeypatch, lambda request: httpx.Response(403))

    asyncio.run(integrity.run_check(1))

    assert finished["status"] == "error"
    assert "szurubooru.test refused the request" in finished["error"]


def test_retries_still_apply_before_giving_up(monkeypatch):
    monkeypatch.setattr(http, "RETRY_DELAYS", (0,))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(ApiError):
        asyncio.run(api_get(mock_client(handler), "http://source.test/posts"))

    assert len(calls) == 2
