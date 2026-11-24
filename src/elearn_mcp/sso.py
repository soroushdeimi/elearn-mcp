"""SSO login flow for elearn.ut.ac.ir (ArvanCloud + sso1.ut.ac.ir + account picker)."""

import json
import time
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from elearn_mcp import arvan

_sleep = time.sleep

BASE = "https://elearn.ut.ac.ir"
SSO = "https://sso1.ut.ac.ir"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class LoginError(Exception):
    pass


class AccountChoiceRequired(LoginError):
    def __init__(self, accounts: list[str]):
        super().__init__(f"multiple accounts available: {', '.join(accounts)}")
        self.accounts = accounts


@dataclass
class Session:
    client: httpx.Client
    sesskey: str
    user_id: int
    student_id: str | None


def new_client() -> httpx.Client:
    return httpx.Client(follow_redirects=True, timeout=30.0, headers={"User-Agent": USER_AGENT})


def _rate_limit_delay(resp: httpx.Response) -> float:
    try:
        data = json.loads(resp.text)
        if isinstance(data, dict) and isinstance(data.get("retryAfterSeconds"), (int, float)):
            return float(data["retryAfterSeconds"])
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    retry_after = resp.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return 1.0


def fetch(client: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
    resp = client.request(method, url, **kw)
    retries = 0
    while resp.status_code == 429:
        if retries >= 3:
            raise LoginError("SSO rate limit, try again later")
        _sleep(min(_rate_limit_delay(resp), 30) + 0.5)
        retries += 1
        resp = client.request(method, url, **kw)
    if arvan.is_challenge(resp.text):
        for name, value in arvan.solve(resp.text).items():
            client.cookies.set(name, value, domain=".ut.ac.ir", path="/")
        resp = client.request(method, url, **kw)
        if arvan.is_challenge(resp.text):
            raise LoginError("challenge not solved")
    return resp


def parse_cfg(html: str) -> dict:
    start = html.find("M.cfg = ")
    if start == -1:
        raise LoginError("no M.cfg")
    start += len("M.cfg = ")
    depth = 0
    for i in range(start, len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(html[start : i + 1])
    raise LoginError("no M.cfg")


def _authenticate(client: httpx.Client, username: str, password: str) -> httpx.Response:
    resp = fetch(client, "GET", f"{BASE}/login/index.php")
    if resp.url.host == "sso1.ut.ac.ir":
        return _post_sso(client, resp, username, password)
    if resp.url.host == "elearn.ut.ac.ir":
        return resp
    raise LoginError(f"expected SSO page, got {resp.url}")


def _post_sso(
    client: httpx.Client, page: httpx.Response, username: str, password: str
) -> httpx.Response:
    soup = BeautifulSoup(page.text, "html.parser")
    return_url = soup.find("input", {"name": "ReturnUrl"})
    token = soup.find("input", {"name": "__RequestVerificationToken"})
    if return_url is None or token is None:
        raise LoginError(f"SSO login form not found (HTTP {page.status_code})")
    return fetch(
        client,
        "POST",
        f"{SSO}/ApiContainer.SSO.RCL1/Account/login",
        data={
            "ReturnUrl": return_url["value"],
            "captcha": "",
            "Username": username,
            "Password": password,
            "button": "login",
            "RememberLogin": "false",
            "__RequestVerificationToken": token["value"],
        },
        headers={"Referer": str(page.url), "Origin": SSO},
    )


def _picker_ids(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [i["value"] for i in soup.find_all("input", {"name": "auth_utoauth2_username"})]


def _post_picker(client: httpx.Client, page: httpx.Response, student_id: str) -> httpx.Response:
    return fetch(
        client,
        "POST",
        str(page.url),
        data={"auth_utoauth2_username": student_id, "continue": "ادامه"},
        headers={"Referer": str(page.url)},
    )


def clone_sso_cookies(client: httpx.Client) -> httpx.Client:
    new = new_client()
    for cookie in client.cookies.jar:
        if "sso1.ut.ac.ir" in cookie.domain or cookie.name.startswith("__arcs"):
            new.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
    return new


def accounts(username: str, password: str, client: httpx.Client | None = None) -> list[str]:
    client = client or new_client()
    resp = _authenticate(client, username, password)
    if resp.url.host == "sso1.ut.ac.ir":
        raise LoginError("SSO rejected the login")
    return _picker_ids(resp.text)


def login(
    username: str, password: str, student_id: str | None = None, client: httpx.Client | None = None
) -> Session:
    client = client or new_client()
    resp = _authenticate(client, username, password)
    if resp.url.host == "sso1.ut.ac.ir":
        soup = BeautifulSoup(resp.text, "html.parser")
        err = soup.find(class_="validation-summary-errors") or soup.find(class_="text-danger")
        detail = f": {err.get_text(strip=True)}" if err else ""
        raise LoginError(f"SSO rejected the login{detail}")
    ids = _picker_ids(resp.text)
    if ids:
        if student_id is None:
            if len(ids) == 1:
                student_id = ids[0]
            else:
                raise AccountChoiceRequired(ids)
        elif student_id not in ids:
            raise LoginError(f"student id {student_id} not available; available: {', '.join(ids)}")
        resp = _post_picker(client, resp, student_id)
    cfg = parse_cfg(resp.text)
    user_id = cfg.get("userId", 0)
    if not isinstance(user_id, int) or user_id <= 0:
        raise LoginError("not logged in")
    return Session(client, cfg["sesskey"], user_id, student_id)
