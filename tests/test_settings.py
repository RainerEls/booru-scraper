import asyncio

import pytest
from fastapi.testclient import TestClient

from app import config, db, main, scrape, settings
from app.sources import danbooru, rule34

KEY_NAME = "RULE34_API_KEY"
URL = "http://new-booru.test/api"
PAGE_ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def saved(monkeypatch):
    rows = {}

    async def get_settings():
        return dict(rows)

    async def save_settings(changes):
        for name, value in changes.items():
            if value is None:
                rows.pop(name, None)
            else:
                rows[name] = value

    async def get_checks():
        return []

    monkeypatch.setattr(db, "get_settings", get_settings)
    monkeypatch.setattr(db, "save_settings", save_settings)
    monkeypatch.setattr(db, "get_checks", get_checks)
    monkeypatch.setattr(config, "SETTINGS_KEY", "test key")
    monkeypatch.setattr(config, "ALLOW_SETTINGS_WITHOUT_LOGIN", True)
    yield rows
    settings.unreadable.clear()
    config.apply({})
    for module in (danbooru, rule34):
        module.configure()


@pytest.fixture
def client(saved):
    return TestClient(main.app)


def save(client, values=None, cleared=(), **options):
    return client.post("/api/settings", json={"values": values or {}, "cleared": list(cleared)}, **options)


def test_saved_secrets_are_encrypted_and_applied_without_a_restart(saved, client):
    assert save(client, {KEY_NAME: "hunter2", "RULE34_USER_ID": "42"}).status_code == 204

    assert "hunter2" not in saved[KEY_NAME]
    assert saved["RULE34_USER_ID"] == "42"
    assert rule34.Rule34Source.credentials == ("hunter2", "42")
    assert rule34.session.params["api_key"] == "hunter2"
    assert scrape.SOURCES["rule34"].configured


def test_saved_secrets_never_reach_the_page(client):
    save(client, {KEY_NAME: "hunter2"})

    page = client.get("/settings").text

    assert "hunter2" not in page
    assert "Saved. Type to replace it." in page


def test_a_lost_key_leaves_the_setting_empty_and_says_so(saved, client, monkeypatch):
    save(client, {KEY_NAME: "hunter2"})
    monkeypatch.setattr(config, "SETTINGS_KEY", "another key")

    asyncio.run(settings.load())

    assert config.RULE34_API_KEY == ""
    assert "can't be decrypted" in client.get("/settings").text


@pytest.mark.parametrize("key", ["", "wrong"])
def test_load_survives_a_missing_or_wrong_key(saved, client, monkeypatch, key):
    save(client, {KEY_NAME: "hunter2"})
    monkeypatch.setattr(config, "SETTINGS_KEY", key)

    asyncio.run(settings.load())

    assert settings.unreadable == {KEY_NAME}


def test_secrets_need_a_key_but_other_settings_do_not(saved, client, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_KEY", "")

    assert save(client, {KEY_NAME: "hunter2"}).status_code == 422
    assert save(client, {"RULE34_USER_ID": "42"}).status_code == 204
    assert KEY_NAME not in saved


def test_env_values_take_priority_and_cannot_be_changed(saved, client, monkeypatch):
    monkeypatch.setenv("RULE34_USER_ID", "from-env")
    saved["RULE34_USER_ID"] = "from-db"

    asyncio.run(settings.load())

    assert config.RULE34_USER_ID == "from-env"
    response = save(client, {"RULE34_USER_ID": "7"})
    assert response.status_code == 422
    assert ".env" in response.json()["detail"]


def test_read_only_without_a_login_or_the_opt_in(saved, client, monkeypatch):
    monkeypatch.setattr(config, "ALLOW_SETTINGS_WITHOUT_LOGIN", False)

    assert save(client, {"RULE34_USER_ID": "42"}).status_code == 403
    assert saved == {}
    assert "read-only" in client.get("/settings").text


def test_cross_site_saves_are_refused_even_without_a_login(saved, client):
    response = save(client, {"RULE34_USER_ID": "42"}, headers={"origin": "http://evil.test"})

    assert response.status_code == 403
    assert saved == {}


def test_a_new_url_needs_the_token_again(saved, client, monkeypatch):
    monkeypatch.delenv("SZURUBOORU_BASE_URL")
    monkeypatch.delenv("SZURUBOORU_API_TOKEN")
    assert save(client, {"SZURUBOORU_BASE_URL": "http://old-booru.test/api", "SZURUBOORU_API_TOKEN": "abc"}).status_code == 204

    assert save(client, {"SZURUBOORU_BASE_URL": URL}).status_code == 422
    assert config.SZURUBOORU_BASE_URL == "http://old-booru.test/api"
    assert save(client, {"SZURUBOORU_BASE_URL": URL, "SZURUBOORU_API_TOKEN": "abc"}).status_code == 204
    assert config.SZURUBOORU_BASE_URL == URL


@pytest.mark.parametrize(
    "name, value",
    [
        ("RULE34_RATE_LIMIT", "fast"),
        ("RULE34_RATE_LIMIT", "-1"),
        ("RULE34_RATE_LIMIT", "nan"),
        ("RULE34_RATE_LIMIT", "inf"),
        ("CONCURRENT_UPLOADS", "0"),
        ("CONCURRENT_UPLOADS", "1.5"),
        ("NOT_A_SETTING", "1"),
    ],
)
def test_bad_values_are_refused(saved, client, name, value):
    assert save(client, {name: value}).status_code == 422
    assert saved == {}


def test_url_scheme_is_checked(saved, client, monkeypatch):
    monkeypatch.delenv("SZURUBOORU_BASE_URL")
    monkeypatch.delenv("SZURUBOORU_API_TOKEN")
    config.apply({})

    assert save(client, {"SZURUBOORU_BASE_URL": "file:///etc/passwd"}).status_code == 422


def test_empty_values_fall_back_to_the_default(saved, client, monkeypatch):
    monkeypatch.delenv("RULE34_RATE_LIMIT")
    assert save(client, {"RULE34_RATE_LIMIT": "2.5"}).status_code == 204
    assert rule34.RATE_LIMIT == 2.5

    assert save(client, {"RULE34_RATE_LIMIT": ""}).status_code == 204

    assert "RULE34_RATE_LIMIT" not in saved
    assert rule34.RATE_LIMIT == 1


def test_secrets_can_be_removed(saved, client):
    save(client, {KEY_NAME: "hunter2"})

    assert save(client, cleared=[KEY_NAME]).status_code == 204

    assert KEY_NAME not in saved
    assert config.RULE34_API_KEY == ""


def test_saving_waits_for_running_work(saved, client, monkeypatch):
    monkeypatch.setattr(main, "active_jobs", lambda: ["job"])

    assert save(client, {"RULE34_USER_ID": "42"}).status_code == 409
    assert saved == {}
