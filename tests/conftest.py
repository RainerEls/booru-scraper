import os

import pytest

os.environ.update(
    DB_USER="test",
    DB_PASS="test",
    DB_HOST="localhost",
    DB_PORT="5432",
    DB_NAME="test",
    APPRISE_URLS="",
    NOTIFY_ON="",
    SZURUBOORU_BASE_URL="http://szurubooru.test/api",
    SZURUBOORU_USER_ID="test",
    SZURUBOORU_API_TOKEN="test",
    GELBOORU_API_KEY="secret-key",
    GELBOORU_USER_ID="1",
    GELBOORU_RATE_LIMIT="0",
    SZURUBOORU_RATE_LIMIT="0",
    YANDERE_RATE_LIMIT="0",
    DANBOORU_RATE_LIMIT="0",
    E621_RATE_LIMIT="0",
    RULE34_RATE_LIMIT="0",
)


@pytest.fixture(autouse=True)
def no_retry_delays(monkeypatch):
    from app import http

    monkeypatch.setattr(http, "RETRY_DELAYS", (0, 0, 0, 0))
