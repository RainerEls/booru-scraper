import asyncio

import httpx
import pytest

from app import http


@pytest.fixture
def waits(monkeypatch):
    monkeypatch.setattr(http, "RETRY_DELAYS", (5, 10, 20, 40))
    recorded = []

    async def sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(http.asyncio, "sleep", sleep)
    return recorded


def get(responses, notices=None):
    def handler(request):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def run():
        if notices is not None:
            http.run_log.set(lambda level, message: notices.append(message))
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return await http.api_get(client, "http://source.test/api", {"api_key": "secret-key"})

    return asyncio.run(run())


def test_backs_off_exponentially_until_a_request_succeeds(waits):
    notices = []
    responses = [httpx.Response(429), httpx.Response(503), httpx.ConnectError("down"), httpx.Response(200)]

    assert get(responses, notices).status_code == 200
    assert waits == [5, 10, 20]
    assert "HTTP 429" in notices[0]
    assert "secret-key" not in " ".join(notices)


def test_honours_retry_after(waits):
    assert get([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)]).status_code == 200
    assert waits == [7]


def test_gives_up_after_the_last_attempt(waits):
    with pytest.raises(http.ApiError) as e:
        get([httpx.Response(429) for _ in range(5)])

    assert waits == [5, 10, 20, 40]
    assert "HTTP 429" in str(e.value)
    assert "secret-key" not in str(e.value)


def test_connection_errors_surface_after_the_last_attempt(waits):
    with pytest.raises(httpx.ConnectError):
        get([httpx.ConnectError("down") for _ in range(5)])


def test_other_client_errors_are_not_retried(waits):
    with pytest.raises(http.ApiError):
        get([httpx.Response(404)])

    assert waits == []
