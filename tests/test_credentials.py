from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest

from parentsquare_mcp import auth


def _completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _status_logged_in() -> subprocess.CompletedProcess:
    return _completed(returncode=0, stdout="Logged in as user@example.com.")


def _lpass_entry(username: str = "parent@example.com", password: str = "s3cret") -> str:
    return json.dumps([{"id": "1", "name": "Parentsquare", "username": username, "password": password}])


# --- LastPass loader ---------------------------------------------------------


def test_lastpass_success(monkeypatch):
    monkeypatch.delenv("PS_LASTPASS_ITEM", raising=False)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["lpass", "status"]:
            return _status_logged_in()
        if cmd[:2] == ["lpass", "show"]:
            assert cmd[-1] == "parentsquare.com"
            return _completed(stdout=_lpass_entry())
        raise AssertionError(f"unexpected command: {cmd}")

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert auth.load_credentials_from_lastpass() == ("parent@example.com", "s3cret")
    assert calls[0][:2] == ["lpass", "status"]


def test_lastpass_custom_item(monkeypatch):
    monkeypatch.setenv("PS_LASTPASS_ITEM", "12345")

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["lpass", "status"]:
            return _status_logged_in()
        assert cmd[-1] == "12345"
        return _completed(stdout=_lpass_entry())

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert auth.load_credentials_from_lastpass() == ("parent@example.com", "s3cret")


def test_lastpass_logged_out():
    def fake_run(cmd, **kwargs):
        return _completed(returncode=1)  # status non-zero

    with mock.patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="Not logged in to LastPass"):
            auth.load_credentials_from_lastpass()


def test_lastpass_cli_missing():
    with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(RuntimeError, match="not found"):
            auth.load_credentials_from_lastpass()


def test_lastpass_status_timeout():
    with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="lpass", timeout=10)):
        with pytest.raises(RuntimeError, match="Timed out"):
            auth.load_credentials_from_lastpass()


def test_lastpass_show_failure():
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["lpass", "status"]:
            return _status_logged_in()
        return _completed(returncode=1)

    with mock.patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="Failed to read"):
            auth.load_credentials_from_lastpass()


def test_lastpass_malformed_json():
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["lpass", "status"]:
            return _status_logged_in()
        return _completed(stdout="not json")

    with mock.patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="parse LastPass response"):
            auth.load_credentials_from_lastpass()


def test_lastpass_no_entries():
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["lpass", "status"]:
            return _status_logged_in()
        return _completed(stdout="[]")

    with mock.patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="No LastPass entry"):
            auth.load_credentials_from_lastpass()


def test_lastpass_duplicate_entries():
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["lpass", "status"]:
            return _status_logged_in()
        return _completed(stdout=json.dumps([{"username": "a", "password": "b"},
                                             {"username": "c", "password": "d"}]))

    with mock.patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="Multiple LastPass entries"):
            auth.load_credentials_from_lastpass()


def test_lastpass_missing_fields():
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["lpass", "status"]:
            return _status_logged_in()
        return _completed(stdout=json.dumps([{"username": "a", "password": ""}]))

    with mock.patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="missing a username or password"):
            auth.load_credentials_from_lastpass()


# --- Routing / precedence ----------------------------------------------------


def test_env_precedence(monkeypatch):
    monkeypatch.setenv("PS_USERNAME", "envuser")
    monkeypatch.setenv("PS_PASSWORD", "envpass")
    monkeypatch.setenv("PS_CREDENTIAL_PROVIDER", "lastpass")
    # subprocess must never be called when env creds are complete
    with mock.patch("subprocess.run", side_effect=AssertionError("should not run")):
        assert auth.load_credentials() == ("envuser", "envpass")


def test_routing_selects_lastpass(monkeypatch):
    monkeypatch.delenv("PS_USERNAME", raising=False)
    monkeypatch.delenv("PS_PASSWORD", raising=False)
    monkeypatch.setenv("PS_CREDENTIAL_PROVIDER", "lastpass")
    with mock.patch.object(auth, "load_credentials_from_lastpass", return_value=("u", "p")) as m:
        assert auth.load_credentials() == ("u", "p")
    m.assert_called_once()


def test_routing_defaults_to_1password(monkeypatch):
    monkeypatch.delenv("PS_USERNAME", raising=False)
    monkeypatch.delenv("PS_PASSWORD", raising=False)
    monkeypatch.delenv("PS_CREDENTIAL_PROVIDER", raising=False)
    with mock.patch.object(auth, "load_credentials_from_1password", return_value=("u", "p")) as m:
        assert auth.load_credentials() == ("u", "p")
    m.assert_called_once()


def test_routing_unknown_provider(monkeypatch):
    monkeypatch.delenv("PS_USERNAME", raising=False)
    monkeypatch.delenv("PS_PASSWORD", raising=False)
    monkeypatch.setenv("PS_CREDENTIAL_PROVIDER", "bitwarden")
    with pytest.raises(RuntimeError, match="Unknown PS_CREDENTIAL_PROVIDER"):
        auth.load_credentials()
