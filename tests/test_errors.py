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
    return httpx.Response(200, json={"results": [], "config": {}})


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
    monkeypatch.setattr(config, "SZURUBOORU_API_TOKEN", "")

    with pytest.raises(SetupProblem, match="Set SZURUBOORU_API_TOKEN in Settings"):
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
    assert error_lines(job) == ["Stopped: fake needs its API key and user id set in Settings."]


def test_a_bad_szurubooru_token_stops_before_searching(monkeypatch, fake_db):
    source = FakeSource([fake_post()])
    booru(monkeypatch, lambda request: httpx.Response(403, json={"name": "AuthError"}))

    job = run_job(monkeypatch, source)

    assert source.yielded == 0
    assert job.status == "error"
    assert "szurubooru.test refused the request (HTTP 403)" in error_lines(job)[0]


def test_a_private_instance_account_that_cannot_list_categories_still_uploads(monkeypatch, fake_db):
    def handler(request):
        url = str(request.url)
        if url.endswith("/info"):
            return httpx.Response(200, json={"config": {}})
        if url.endswith("/posts/"):
            return httpx.Response(200, json={"id": 7})
        return httpx.Response(403, json={"name": "AuthError"})

    booru(monkeypatch, handler)

    job = run_job(monkeypatch, FakeSource([fake_post(tags="cat")], ["cat"]))

    assert job.status == "done"
    assert job.counters["processed"] == 1
    assert warnings_of(job) == [
        "The szurubooru account can't manage tag categories, so missing ones won't be created.",
        "The szurubooru account can't read or create tags, so no tags will be written.",
    ]


def test_a_duplicate_upload_without_edit_rights_is_skipped_not_fatal(monkeypatch, fake_db):
    def handler(request):
        url = str(request.url)
        if url.endswith("/posts/"):
            return httpx.Response(400, json={"name": "PostAlreadyUploadedError", "otherPostId": 12})
        if "/post/12" in url:
            return httpx.Response(403, json={"name": "AuthError"})
        return httpx.Response(404 if "/tag/" in url else 200, json={"results": [], "config": {}})

    booru(monkeypatch, handler)
    source = FakeSource([fake_post(1), fake_post(2)], [])
    source.post_page = "http://fake.test/{id}"

    job = run_job(monkeypatch, source)

    assert job.status == "done"
    assert job.counters["skipped"] == 2
    assert warnings_of(job) == [scrape.NO_POST_EDITS]
    assert fake_db.posts[("fake", 1)]["status"] == "skipped"


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
        "Stopped: source.test refused the request (HTTP 401). Check the credentials for it in Settings."
    ]


def test_a_rejected_upload_cancels_the_rest_of_the_run(monkeypatch, fake_db):
    monkeypatch.setattr(config, "CONCURRENT_UPLOADS", 1)
    uploads = []

    def handler(request):
        if str(request.url).endswith("/posts/"):
            uploads.append(request)
            return httpx.Response(403, json={"name": "AuthError"})
        return httpx.Response(200, json={"results": [], "version": 1, "config": {}})

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
        return httpx.Response(200, json={"results": [], "version": 1, "config": {}})

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
    monkeypatch.setattr(config, "SZURUBOORU_BASE_URL", "")
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


def tag_booru(monkeypatch, existing=(), post_tag=200, put_tag=200, post_status=200):
    calls = []

    def handler(request):
        url = str(request.url)
        calls.append((request.method, url))
        if "/tag/" in url:
            name = url.rsplit("/", 1)[1]
            if request.method == "PUT":
                return httpx.Response(put_tag, json={})
            if name in existing:
                return httpx.Response(200, json={"version": 1, "category": existing[name]})
            return httpx.Response(404, json={})
        if url.endswith("/tags"):
            return httpx.Response(post_tag, json={})
        if url.endswith("/posts/"):
            return httpx.Response(post_status, json={"id": 7})
        return categories_ok(request)

    booru(monkeypatch, handler)
    return calls


def warnings_of(job):
    return [line["message"] for line in job.lines if line["level"] == "warning"]


def test_a_tag_with_the_right_category_is_not_written(monkeypatch, fake_db):
    calls = tag_booru(monkeypatch, existing={"cat": "general"})

    run_job(monkeypatch, FakeSource([fake_post(tags="cat")], ["cat"]))

    assert not [call for call in calls if call[0] == "PUT"]
    assert "cat" in fake_db.tags


def test_no_edit_permission_warns_once_and_still_creates_new_tags(monkeypatch, fake_db):
    existing = {"a": "artist", "b": "artist", "c": "artist"}
    calls = tag_booru(monkeypatch, existing=existing, put_tag=403)

    job = run_job(monkeypatch, FakeSource([fake_post(tags="a b c new")], ["a", "b", "c", "new"]))

    assert warnings_of(job) == ["The szurubooru account can't edit tags, so existing tags keep their category."]
    assert ("POST", "http://szurubooru.test/api/tags") in calls
    assert fake_db.tags == {"a", "b", "c", "new"}
    assert job.counters["processed"] == 1
    assert job.status == "done"


def test_no_create_permission_warns_once_and_stops_writing_tags(monkeypatch, fake_db):
    calls = tag_booru(monkeypatch, post_tag=403)

    job = run_job(monkeypatch, FakeSource([fake_post(tags="a b c")], ["a", "b", "c"]))

    assert warnings_of(job) == ["The szurubooru account can't read or create tags, so no tags will be written."]
    assert len([call for call in calls if call == ("POST", "http://szurubooru.test/api/tags")]) == 1
    assert not fake_db.tags
    assert job.counters["processed"] == 1


def merge_setup(monkeypatch, fake_db, handler, count=3):
    fake_db.posts[("other", 9)] = {"md5": "same", "status": "processed", "szurubooru_post_id": 7}
    posts = [{**fake_post(id, tags="dog"), "md5": "same"} for id in range(1, count + 1)]
    source = FakeSource(posts, ["dog"])
    source.post_page = "http://fake.test/{id}"
    booru(monkeypatch, handler)
    return run_job(monkeypatch, source)


def test_no_post_edit_permission_warns_once_and_keeps_duplicates_for_later(monkeypatch, fake_db):
    def handler(request):
        if "/tag/" in str(request.url):
            return httpx.Response(404, json={})
        if request.method == "PUT" and "/post/" in str(request.url):
            return httpx.Response(403, json={})
        if "/post/" in str(request.url):
            return httpx.Response(200, json={"version": 1, "tags": [], "source": None})
        return categories_ok(request)

    job = merge_setup(monkeypatch, fake_db, handler)

    assert warnings_of(job) == ["The szurubooru account can't edit posts, so duplicates won't be merged."]
    assert ("fake", 1) not in fake_db.posts


def test_a_duplicate_of_a_deleted_booru_post_is_not_retried(monkeypatch, fake_db):
    def handler(request):
        if "/post/" in str(request.url) or "/tag/" in str(request.url):
            return httpx.Response(404, json={})
        return categories_ok(request)

    job = merge_setup(monkeypatch, fake_db, handler, count=1)

    assert fake_db.posts[("fake", 1)]["status"] == "skipped"
    assert not warnings_of(job)


def test_tag_categories_match_whatever_their_case(monkeypatch, fake_db):
    calls = []

    def handler(request):
        url = str(request.url)
        calls.append((request.method, url))
        if url.endswith("/tag-categories"):
            return httpx.Response(200, json={"results": [{"name": "General"}, {"name": "Artist"}]})
        if "/tag/" in url:
            return httpx.Response(200, json={"version": 1, "category": "General"})
        if url.endswith("/posts/"):
            return httpx.Response(200, json={"id": 7})
        return httpx.Response(200, json={"config": {}})

    booru(monkeypatch, handler)
    source = FakeSource([fake_post(tags="cat")], ["cat"])
    source.tag_categories = {0: "general", 1: "artist"}

    job = run_job(monkeypatch, source)

    assert not warnings_of(job)
    assert not [call for call in calls if call[0] == "PUT" or call[1].endswith("/tag-categories") and call[0] == "POST"]


def test_a_rate_limited_file_check_waits_and_tries_again(monkeypatch):
    responses = [httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(429), httpx.Response(200)]
    booru(monkeypatch, lambda request: responses.pop(0))

    assert asyncio.run(szurubooru.head_file("data/posts/1.png")).status_code == 200
    assert not responses


def test_a_file_check_gives_up_once_the_retries_run_out(monkeypatch):
    booru(monkeypatch, lambda request: httpx.Response(429))

    assert asyncio.run(szurubooru.head_file("data/posts/1.png")).status_code == 429
