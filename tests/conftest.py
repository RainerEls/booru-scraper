import os

import pytest

os.environ.update(
    DB_USER="test",
    DB_PASS="test",
    DB_HOST="localhost",
    DB_PORT="5432",
    DB_NAME="test",
    APP_PASSWORD="",
    COOKIE_SECURE="false",
    APPRISE_URLS="",
    NOTIFY_ON="",
    SZURUBOORU_BASE_URL="http://szurubooru.test/api",
    SZURUBOORU_USER_ID="test",
    SZURUBOORU_API_TOKEN="test",
    GELBOORU_API_KEY="secret-key",
    GELBOORU_USER_ID="1",
    DANBOORU_USER_ID="",
    DANBOORU_API_KEY="",
    E621_USER_ID="",
    E621_API_KEY="",
    RULE34_USER_ID="",
    RULE34_API_KEY="",
    YANDERE_USER_ID="",
    YANDERE_PASSWORD_HASH="",
    SETTINGS_KEY="",
    ALLOW_SETTINGS_WITHOUT_LOGIN="false",
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
