import subprocess

import pytest

from elearn_mcp import moodle, sso
from elearn_mcp.accounts import Accounts, load_credentials


@pytest.fixture(autouse=True)
def fake_clone(monkeypatch):
    monkeypatch.setattr(sso, "clone_sso_cookies", lambda client: object(), raising=False)


def make_accounts(login=None, list_accounts=None):
    calls = {"login": 0, "list": 0}

    def fake_login(username, password, student_id=None, client=None):
        calls["login"] += 1
        return sso.Session(client=object(), sesskey="k", user_id=1, student_id=student_id)

    def fake_list(username, password, client=None):
        calls["list"] += 1
        return ["1001", "1002"]

    acc = Accounts("u", "p", login=login or fake_login, list_accounts=list_accounts or fake_list)
    return acc, calls


def test_ids_cached():
    acc, calls = make_accounts()
    assert acc.ids() == ["1001", "1002"]
    assert acc.ids() == ["1001", "1002"]
    assert calls["list"] == 1


def test_session_logs_in_once_per_id():
    acc, calls = make_accounts()
    s1 = acc.session("1001")
    s2 = acc.session("1001")
    assert s1 is s2
    assert calls["login"] == 1
    acc.session("1002")
    assert calls["login"] == 2


def test_session_unknown_id_raises():
    acc, _ = make_accounts()
    with pytest.raises(ValueError):
        acc.session("9999")


def test_run_returns_fn_result():
    acc, _ = make_accounts()
    result = acc.run("1001", lambda s: "ok")
    assert result == "ok"


def test_run_relogs_in_once_on_session_expired():
    acc, calls = make_accounts()
    results = iter(["first", "second"])

    def fn(session):
        value = next(results)
        if value == "first":
            raise moodle.SessionExpired("expired", "requireloginerror")
        return value

    assert acc.run("1001", fn) == "second"
    assert calls["login"] == 2


def test_run_second_session_expired_raises():
    acc, _ = make_accounts()

    def fn(session):
        raise moodle.SessionExpired("expired", "requireloginerror")

    with pytest.raises(moodle.SessionExpired):
        acc.run("1001", fn)


def test_load_credentials_from_env(monkeypatch):
    monkeypatch.setenv("ELEARN_USERNAME", "u")
    monkeypatch.setenv("ELEARN_PASSWORD", "p")
    assert load_credentials() == ("u", "p")


def test_load_credentials_missing_username(monkeypatch):
    monkeypatch.delenv("ELEARN_USERNAME", raising=False)
    monkeypatch.setenv("ELEARN_PASSWORD", "p")
    with pytest.raises(RuntimeError, match="ELEARN_USERNAME"):
        load_credentials()


def test_load_credentials_missing_both(monkeypatch):
    monkeypatch.delenv("ELEARN_USERNAME", raising=False)
    monkeypatch.delenv("ELEARN_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="ELEARN_USERNAME"):
        load_credentials()


def test_load_credentials_from_keychain(monkeypatch):
    monkeypatch.setenv("ELEARN_USERNAME", "u")
    monkeypatch.delenv("ELEARN_PASSWORD", raising=False)
    argv = []

    def fake_run(*a, **kw):
        argv.extend(a)
        return subprocess.CompletedProcess(a, 0, stdout="secret\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert load_credentials() == ("u", "secret")
    assert argv[0] == ["security", "find-generic-password", "-s", "elearn-mcp", "-a", "u", "-w"]


def test_load_credentials_keychain_failure(monkeypatch):
    monkeypatch.setenv("ELEARN_USERNAME", "u")
    monkeypatch.delenv("ELEARN_PASSWORD", raising=False)

    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(a, 44, stdout="", stderr="not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as e:
        load_credentials()
    assert "secret" not in str(e.value)


def test_load_credentials_without_keychain(monkeypatch):
    monkeypatch.setenv("ELEARN_USERNAME", "u@ut.ac.ir")
    monkeypatch.delenv("ELEARN_PASSWORD", raising=False)

    def missing(*a, **k):
        raise FileNotFoundError("security")

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="ELEARN_PASSWORD"):
        load_credentials()
