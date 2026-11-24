import json
import subprocess

import httpx
import pytest
import respx

from elearn_mcp import notify
from elearn_mcp.notify import NotifyError


def test_secret_env_first(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    assert notify.secret("TELEGRAM_BOT_TOKEN") == "env-token"


def test_secret_keychain_argv(monkeypatch):
    monkeypatch.delenv("KAVENEGAR_API_KEY", raising=False)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="keychain-key\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert notify.secret("KAVENEGAR_API_KEY") == "keychain-key"
    expected = [
        ["security", "find-generic-password", "-s", "elearn-mcp", "-a", "KAVENEGAR_API_KEY", "-w"]
    ]
    assert calls == expected


def test_secret_missing(monkeypatch):
    monkeypatch.delenv("NO_SUCH_SECRET", raising=False)

    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(a[0], 44, stdout="", stderr="err")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert notify.secret("NO_SUCH_SECRET") is None


def test_macos_escaping(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    notify.macos('Title "with" quotes', 'text with \\ backslash and "quotes"')
    script = calls[0][2]
    expected = (
        'display notification "text with \\\\ backslash and \\"quotes\\""'
        ' with title "Title \\"with\\" quotes"'
    )
    assert script == expected


def test_macos_failure_raises(monkeypatch):
    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(a[0], 1, stdout="", stderr="x")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(NotifyError):
        notify.macos("t", "x")


@respx.mock
def test_telegram_request(monkeypatch):
    route = respx.post("https://api.telegram.org/botTOKEN123/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    notify.telegram("hello", token="TOKEN123", chat_id="42")
    request = route.calls.last.request
    expected = {"chat_id": "42", "text": "hello", "disable_web_page_preview": True}
    assert json.loads(request.content) == expected


@respx.mock
def test_telegram_error_shows_description_not_token(monkeypatch):
    body = {"ok": False, "description": "Bad Request: chat not found"}
    respx.post("https://api.telegram.org/botSECRET_TOKEN/sendMessage").mock(
        return_value=httpx.Response(400, json=body)
    )
    with pytest.raises(NotifyError) as e:
        notify.telegram("hello", token="SECRET_TOKEN", chat_id="42")
    assert "chat not found" in str(e.value)
    assert "SECRET_TOKEN" not in str(e.value)


@respx.mock
def test_telegram_http_error_no_token(monkeypatch):
    respx.post("https://api.telegram.org/botSECRET_TOKEN/sendMessage").mock(
        return_value=httpx.Response(502, text="bad gateway")
    )
    with pytest.raises(NotifyError) as e:
        notify.telegram("hello", token="SECRET_TOKEN", chat_id="42")
    assert "SECRET_TOKEN" not in str(e.value)


def test_telegram_not_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(notify, "secret", lambda name: None)
    with pytest.raises(NotifyError, match="telegram is not configured"):
        notify.telegram("hello")


@respx.mock
def test_sms_request_form(monkeypatch):
    route = respx.post("https://api.kavenegar.com/v1/KEY456/sms/send.json").mock(
        return_value=httpx.Response(200, json={"return": {"status": 200, "message": "OK"}})
    )
    notify.sms("hello", receptor="09121234567", api_key="KEY456", sender="SENDER")
    body = route.calls.last.request.content.decode()
    assert "receptor=09121234567" in body
    assert "message=hello" in body
    assert "sender=SENDER" in body


@respx.mock
def test_sms_message_cut_to_600(monkeypatch):
    route = respx.post("https://api.kavenegar.com/v1/KEY456/sms/send.json").mock(
        return_value=httpx.Response(200, json={"return": {"status": 200, "message": "OK"}})
    )
    notify.sms("x" * 700, receptor="09121234567", api_key="KEY456")
    body = route.calls.last.request.content.decode()
    assert "message=" + "x" * 600 in body
    assert "x" * 601 not in body


@respx.mock
def test_sms_status_not_200_no_key(monkeypatch):
    respx.post("https://api.kavenegar.com/v1/SECRET_KEY/sms/send.json").mock(
        return_value=httpx.Response(200, json={"return": {"status": 401, "message": "invalid key"}})
    )
    with pytest.raises(NotifyError) as e:
        notify.sms("hello", receptor="09121234567", api_key="SECRET_KEY")
    assert "invalid key" in str(e.value)
    assert "SECRET_KEY" not in str(e.value)


def test_sms_not_configured(monkeypatch):
    monkeypatch.setattr(notify, "secret", lambda name: None)
    with pytest.raises(NotifyError, match="sms is not configured"):
        notify.sms("hello")


def test_send_continues_after_failing_channel(monkeypatch):
    def broken(title, text):
        raise NotifyError("mac broken")

    monkeypatch.setattr(notify, "macos", broken)
    monkeypatch.setattr(notify, "telegram", lambda text: None)
    results = notify.send("hi", ["macos", "telegram", "pager"])
    assert results == {"macos": "mac broken", "telegram": "ok", "pager": "unknown channel"}


def test_send_all_ok(monkeypatch):
    monkeypatch.setattr(notify, "macos", lambda title, text: None)
    monkeypatch.setattr(notify, "telegram", lambda text: None)
    monkeypatch.setattr(notify, "sms", lambda text: None)
    assert notify.send("hi", ["macos", "telegram", "sms"], title="t") == {
        "macos": "ok",
        "telegram": "ok",
        "sms": "ok",
    }


def test_secret_without_keychain_returns_none(monkeypatch):
    monkeypatch.delenv("NOPE_SECRET", raising=False)

    def missing(*a, **k):
        raise FileNotFoundError("security")

    monkeypatch.setattr(notify.subprocess, "run", missing)
    assert notify.secret("NOPE_SECRET") is None
    with pytest.raises(notify.NotifyError, match="only work on macOS"):
        notify.macos("t", "x")


def test_send_rejects_channels_string():
    with pytest.raises(notify.NotifyError, match="must be a list"):
        notify.send("hi", "telegram")
