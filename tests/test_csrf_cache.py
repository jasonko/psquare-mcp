"""Tests for PSClient CSRF token caching and single-retry refresh."""

from types import SimpleNamespace

import pytest
import requests

from parentsquare_mcp.client import PSClient

DASHBOARD = '<html><head><meta name="csrf-token" content="{token}"></head></html>'


class FakeSession:
    """Records requests; serves a dashboard for GET / and canned write replies."""

    def __init__(self, tokens=("tok-1", "tok-2", "tok-3"), replies=None):
        self.tokens = list(tokens)
        self.replies = list(replies or [])
        self.dashboard_gets = 0
        self.calls = []  # (method, url, csrf_header)
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()

    def _dashboard(self):
        self.dashboard_gets += 1
        token = self.tokens[min(self.dashboard_gets, len(self.tokens)) - 1]
        return SimpleNamespace(url="https://www.parentsquare.com/",
                               text=DASHBOARD.format(token=token), status_code=200)

    def get(self, url, params=None, **kw):
        return self._dashboard()

    def _write(self, method, url, **kw):
        self.calls.append((method, url, kw.get("headers", {}).get("X-CSRF-Token")))
        if self.replies:
            return self.replies.pop(0)
        return _resp(200)

    def post(self, url, **kw):
        return self._write("POST", url, **kw)

    def request(self, method, url, **kw):
        return self._write(method, url, **kw)


def _resp(status, text="{}", url="https://www.parentsquare.com/api/v2/x"):
    return SimpleNamespace(status_code=status, text=text, url=url)


@pytest.fixture
def client(monkeypatch):
    session = FakeSession()
    c = PSClient(session=session)
    monkeypatch.setattr(c, "_save_cookies_if_changed", lambda: None)
    return c


# --- caching -----------------------------------------------------------------

def test_token_fetched_once_and_reused(client):
    for _ in range(3):
        client.send_json("PUT", "/api/v2/x", {})
    assert client.session.dashboard_gets == 1
    assert [c[2] for c in client.session.calls] == ["tok-1"] * 3


def test_cache_is_shared_across_write_methods(client):
    client.post_form("/schools/1/students", {"a": "b"})
    client.send_json("PATCH", "/api/v2/x", {})
    client.post_json_raw("/schools/1/users/invite", {})
    assert client.session.dashboard_gets == 1


def test_post_form_body_uses_the_cached_token(monkeypatch):
    session = FakeSession()
    bodies = []
    orig = session.post

    def spy(url, **kw):
        bodies.append(kw.get("data"))
        return orig(url, **kw)

    session.post = spy
    c = PSClient(session=session)
    monkeypatch.setattr(c, "_save_cookies_if_changed", lambda: None)
    c.post_form("/schools/1/students", {"student[first_name]": "A"})
    c.post_form("/schools/1/students", {"student[first_name]": "B"})
    assert [b["authenticity_token"] for b in bodies] == ["tok-1", "tok-1"]
    assert all(b["utf8"] == "\u2713" for b in bodies)


def test_invalidate_forces_a_refetch(client):
    client.send_json("PUT", "/api/v2/x", {})
    client.invalidate_csrf_token()
    client.send_json("PUT", "/api/v2/x", {})
    assert client.session.dashboard_gets == 2
    assert [c[2] for c in client.session.calls] == ["tok-1", "tok-2"]


def test_relogin_clears_the_cached_token(client, monkeypatch):
    client.send_json("PUT", "/api/v2/x", {})
    assert client._csrf_token == "tok-1"
    monkeypatch.setattr("parentsquare_mcp.auth.load_credentials", lambda: ("u", "p"))
    monkeypatch.setattr("parentsquare_mcp.auth.login", lambda *a: None)
    client._relogin()
    assert client._csrf_token is None


# --- rejection detection -----------------------------------------------------

@pytest.mark.parametrize("resp", [
    _resp(422, "ActionController::InvalidAuthenticityToken"),
    _resp(422, "Can't verify CSRF token authenticity"),
    _resp(403, "invalid authenticity token"),
    _resp(401, "unauthorized"),
    _resp(200, "<html>sign in</html>", url="https://www.parentsquare.com/signin"),
])
def test_detects_csrf_rejection(resp):
    assert PSClient._is_csrf_rejection(resp) is True


@pytest.mark.parametrize("resp", [
    _resp(200),
    _resp(204, ""),
    _resp(422, '{"errors":[{"detail":"Name has already been taken"}]}'),
    _resp(404, "<html>Not found</html>"),
    _resp(500, "<html>Server error</html>"),
])
def test_ignores_ordinary_failures(resp):
    """A plain validation error must not trigger a retry — the write may have applied."""
    assert PSClient._is_csrf_rejection(resp) is False


# --- retry -------------------------------------------------------------------

def test_retries_once_with_a_fresh_token(monkeypatch):
    session = FakeSession(replies=[_resp(422, "InvalidAuthenticityToken"), _resp(200)])
    c = PSClient(session=session)
    monkeypatch.setattr(c, "_save_cookies_if_changed", lambda: None)
    resp = c.send_json("PUT", "/api/v2/x", {})
    assert resp.status_code == 200
    assert [call[2] for call in session.calls] == ["tok-1", "tok-2"]
    assert session.dashboard_gets == 2
    assert c._csrf_token == "tok-2"


def test_does_not_retry_more_than_once(monkeypatch):
    session = FakeSession(replies=[_resp(401), _resp(401), _resp(200)])
    c = PSClient(session=session)
    monkeypatch.setattr(c, "_save_cookies_if_changed", lambda: None)
    resp = c.send_json("PUT", "/api/v2/x", {})
    assert resp.status_code == 401
    assert len(session.calls) == 2


def test_no_retry_on_a_validation_error(monkeypatch):
    session = FakeSession(replies=[_resp(422, '{"errors":[{"detail":"bad"}]}'), _resp(200)])
    c = PSClient(session=session)
    monkeypatch.setattr(c, "_save_cookies_if_changed", lambda: None)
    resp = c.send_json("PUT", "/api/v2/x", {})
    assert resp.status_code == 422
    assert len(session.calls) == 1


def test_expired_session_is_recovered_on_retry(monkeypatch):
    """Caching removed the implicit GET / per write, so expiry must surface here."""
    session = FakeSession(replies=[
        _resp(200, "<html>sign in</html>", url="https://www.parentsquare.com/signin"),
        _resp(200),
    ])
    c = PSClient(session=session)
    monkeypatch.setattr(c, "_save_cookies_if_changed", lambda: None)
    c._csrf_token = "stale"
    resp = c.send_json("PUT", "/api/v2/x", {})
    assert resp.status_code == 200
    assert [call[2] for call in session.calls] == ["stale", "tok-1"]
