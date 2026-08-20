"""Pins the Accept header each PSClient request helper sends.

The Rails roster feeds (``/schools/{id}/roster/{students,parents,staff}_data``)
back ``list_students`` / ``list_parents`` / ``list_staff`` and are served by a
``respond_to`` block with no JS template. Offering ``text/javascript`` in
``Accept`` makes Rails pick the JS format and return 404, silently taking those
three tools down — so ``get_json`` must keep its Accept JSON-only, even though
the browser (and therefore the ``/api/v2/`` write helpers) sends the wider
``application/json, text/javascript, */*; q=0.01``.
"""

import json as jsonlib
from types import SimpleNamespace

import pytest
import requests

from parentsquare_mcp.client import PSClient

ROSTER_FEED = "/schools/13749/roster/parents_data"
API_V2 = "/api/v2/schools/13749"


class _Resp:
    def __init__(self, status, payload=None, url="https://www.parentsquare.com/"):
        self.status_code = status
        self.url = url
        self.text = jsonlib.dumps(payload) if payload is not None else ""
        self._payload = payload
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class RailsRespondToSession:
    """Fake session mimicking the roster feed's ``respond_to`` content negotiation.

    Rails picks the first format the client offers that it knows about. The
    roster feeds register JSON but have no JS template, so an Accept naming
    ``text/javascript`` resolves to a missing template and 404s.
    """

    def __init__(self):
        self.accepts = []
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()

    def get(self, url, params=None, headers=None, **kw):
        accept = (headers or {}).get("Accept", "")
        self.accepts.append(accept)
        if "roster/" in url and "text/javascript" in accept:
            return _Resp(404, url=url)
        return _Resp(200, {"data": "ok"}, url=url)


@pytest.fixture
def client(monkeypatch):
    c = PSClient(session=RailsRespondToSession())
    monkeypatch.setattr(c, "_save_cookies_if_changed", lambda: None)
    return c


def test_get_json_reaches_the_roster_feed(client):
    """The end the rule actually protects: list_parents et al. keep working."""
    assert client.get_json(ROSTER_FEED) == {"data": "ok"}


def test_get_json_accept_is_json_only(client):
    client.get_json(ROSTER_FEED)
    assert client.session.accepts == ["application/json"]


def test_widening_get_json_accept_would_404_the_roster_feed():
    """Guards the reason for the rule, so removing it can't look harmless.

    If someone "harmonises" get_json onto the browser's Accept, this is what
    happens to the roster feeds.
    """
    session = RailsRespondToSession()
    browser_accept = {"Accept": "application/json, text/javascript, */*; q=0.01"}
    assert session.get(f"https://www.parentsquare.com{ROSTER_FEED}",
                       headers=browser_accept).status_code == 404
    assert session.get(f"https://www.parentsquare.com{API_V2}",
                       headers=browser_accept).status_code == 200


@pytest.mark.parametrize("send", [
    lambda c: c.send_json("PATCH", API_V2, {}),
    lambda c: c.post_json_raw(API_V2, {}),
])
def test_api_v2_write_helpers_keep_the_wider_browser_accept(monkeypatch, send):
    """The rule is one-directional — don't "fix" these to match get_json.

    The /api/v2/ endpoints accept either header; these mirror the browser.
    """
    seen = {}

    def _capture(method, url, **kw):
        seen["accept"] = kw["headers"]["Accept"]
        return SimpleNamespace(status_code=200, text="{}", url=url, headers={})

    session = SimpleNamespace(
        request=_capture,
        post=lambda url, **kw: _capture("POST", url, **kw),
        headers={},
        cookies=requests.cookies.RequestsCookieJar(),
    )
    c = PSClient(session=session)
    monkeypatch.setattr(c, "_save_cookies_if_changed", lambda: None)
    monkeypatch.setattr(c, "_get_csrf_token", lambda force_refresh=False: "tok")

    send(c)
    assert seen["accept"] == "application/json, text/javascript, */*; q=0.01"
