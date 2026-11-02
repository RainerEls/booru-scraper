import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import auth, config, main

PASSWORD = "correct horse"
OPEN_ROUTES = {"/login", "/health"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", PASSWORD)
    monkeypatch.setattr(auth, "sessions", {})
    monkeypatch.setattr(auth, "failures", {})

    async def ping():
        return None

    monkeypatch.setattr(main.db, "ping", ping)
    return TestClient(main.app, follow_redirects=False)


def log_in(client, password=PASSWORD):
    return client.post("/login", data={"password": password})


def test_login_is_off_without_a_password(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    client = TestClient(main.app, follow_redirects=False)

    assert client.get("/api/sources").status_code == 200
    assert client.get("/login").status_code == 303


def protected_routes():
    for route in main.app.routes:
        if isinstance(route, APIRoute) and route.path not in OPEN_ROUTES:
            for method in route.methods:
                yield method, route.path.replace("{check_id}", "1").replace("{run_id}", "1").replace(
                    "{schedule_id}", "1"
                ).replace("{job_id}", "1")


@pytest.mark.parametrize("method, path", sorted(set(protected_routes())))
def test_every_route_needs_a_login(client, method, path):
    assert client.request(method, path).status_code == 401


@pytest.mark.parametrize("path", ["/docs", "/openapi.json", "/nothing-here"])
def test_framework_routes_need_a_login(client, path):
    assert client.get(path).status_code == 401


def test_pages_redirect_to_the_login_form(client):
    response = client.get("/", headers={"Accept": "text/html"})

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_health_and_static_files_stay_open(client):
    assert client.get("/login").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


def test_the_right_password_opens_the_app_until_logout(client):
    assert client.get("/api/sources").status_code == 401

    response = log_in(client)

    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" not in cookie
    assert client.get("/api/sources").status_code == 200

    token = client.cookies[auth.COOKIE]
    client.post("/logout")
    client.cookies.set(auth.COOKIE, token)
    assert client.get("/api/sources").status_code == 401


def test_cookie_secure_marks_the_cookie(client, monkeypatch):
    monkeypatch.setattr(config, "COOKIE_SECURE", True)

    assert "Secure" in log_in(client).headers["set-cookie"]


def test_a_wrong_password_or_forged_cookie_opens_nothing(client):
    assert log_in(client, "wrong").status_code == 401
    assert auth.COOKIE not in client.cookies

    client.cookies.set(auth.COOKIE, "forged")
    assert client.get("/api/sources").status_code == 401


def test_an_expired_session_needs_a_new_login(client):
    log_in(client)
    auth.sessions[client.cookies[auth.COOKIE]] = 0

    assert client.get("/api/sources").status_code == 401


def test_repeated_failures_lock_out_even_the_right_password(client, monkeypatch):
    for _ in range(auth.MAX_FAILURES):
        assert log_in(client, "wrong").status_code == 401

    assert log_in(client).status_code == 429

    client_key = next(iter(auth.failures))
    auth.failures[client_key] = (auth.MAX_FAILURES, auth.failures[client_key][1] - auth.LOCKOUT_SECONDS)
    assert log_in(client).status_code == 303
    assert not auth.failures


def test_a_lockout_leaves_existing_sessions_working(client):
    log_in(client)
    for _ in range(auth.MAX_FAILURES):
        log_in(client, "wrong")

    assert client.get("/api/sources").status_code == 200


def test_cross_site_writes_are_refused_even_when_logged_in(client):
    log_in(client)
    other_site = {"Origin": "https://evil.test"}

    assert client.post("/logout", headers=other_site).status_code == 403
    assert client.post("/login", data={"password": PASSWORD}, headers=other_site).status_code == 403
    assert client.get("/api/sources", headers=other_site).status_code == 200
    assert client.post("/logout", headers={"Origin": "http://testserver"}).status_code == 303


def test_an_oversized_login_form_is_refused(client):
    assert client.post("/login", content=b"password=" + b"x" * auth.MAX_FORM_BYTES).status_code == 413


def test_pages_link_their_stylesheet_so_it_loads_behind_an_https_proxy(client):
    assert 'href="/static/styles.css"' in client.get("/login").text
