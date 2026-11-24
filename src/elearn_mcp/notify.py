"""Notification channels: macOS notification, Telegram, Kavenegar SMS."""

import os
import subprocess

import httpx

TIMEOUT = 20


class NotifyError(Exception):
    pass


def secret(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "elearn-mcp", "-a", name, "-w"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:  # no Keychain outside macOS
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def macos(title: str, text: str) -> None:
    script = f'display notification "{_esc(text)}" with title "{_esc(title)}"'
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    except FileNotFoundError as e:
        raise NotifyError("macos notifications only work on macOS") from e
    if result.returncode != 0:
        raise NotifyError("macos notification failed")


def telegram(text: str, token: str | None = None, chat_id: str | None = None) -> None:
    token = token or secret("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise NotifyError("telegram is not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=TIMEOUT,
        )
        data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        raise NotifyError(f"telegram request failed: {e.__class__.__name__}") from e
    if response.status_code >= 400 or not data.get("ok"):
        raise NotifyError(f"telegram: {data.get('description', f'HTTP {response.status_code}')}")


def sms(
    text: str,
    receptor: str | None = None,
    api_key: str | None = None,
    sender: str | None = None,
) -> None:
    api_key = api_key or secret("KAVENEGAR_API_KEY")
    receptor = receptor or secret("SMS_RECEPTOR")
    sender = sender or secret("KAVENEGAR_SENDER")
    if not api_key or not receptor:
        raise NotifyError("sms is not configured: set KAVENEGAR_API_KEY and SMS_RECEPTOR")
    form = {"receptor": receptor, "message": text[:600]}
    if sender:
        form["sender"] = sender
    try:
        response = httpx.post(
            f"https://api.kavenegar.com/v1/{api_key}/sms/send.json",
            data=form,
            timeout=TIMEOUT,
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as e:
        raise NotifyError(f"sms request failed: {e.__class__.__name__}") from e
    ret = payload.get("return", {})
    if ret.get("status") != 200:
        raise NotifyError(f"sms: {ret.get('message', 'unknown error')}")


def send(text: str, channels: list[str], title: str = "elearn") -> dict:
    if isinstance(channels, str):
        raise NotifyError('channels must be a list, e.g. ["telegram"]')
    results = {}
    for channel in channels:
        try:
            if channel == "macos":
                macos(title, text)
            elif channel == "telegram":
                telegram(text)
            elif channel == "sms":
                sms(text)
            else:
                results[channel] = "unknown channel"
                continue
            results[channel] = "ok"
        except NotifyError as e:
            results[channel] = str(e)
        except Exception as e:  # one failing channel must not stop the others
            results[channel] = f"{channel} failed: {e.__class__.__name__}"
    return results
