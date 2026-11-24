"""Moodle AJAX client for elearn.ut.ac.ir."""

import pathlib
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from . import arvan

BASE = "https://elearn.ut.ac.ir"
SESSION_EXPIRED_CODES = {"servicerequireslogin", "invalidsesskey", "requireloginerror"}


class MoodleError(Exception):
    def __init__(self, message: str, errorcode: str | None = None):
        super().__init__(message)
        self.errorcode = errorcode


class SessionExpired(MoodleError):
    pass


def _request(session, method: str, url: str, **kw) -> httpx.Response:
    response = session.client.request(method, url, **kw)
    if arvan.is_challenge(response.text):
        # challenge cookie lives 900 s, so any request can hit it
        cookies = arvan.solve(response.text)
        for name, value in cookies.items():
            session.client.cookies.set(name, value, domain=".ut.ac.ir", path="/")
        response = session.client.request(method, url, **kw)
        if arvan.is_challenge(response.text):
            raise MoodleError("challenge not solved")
    return response


def call(session, method: str, args: dict):
    url = f"{BASE}/lib/ajax/service.php?sesskey={session.sesskey}&info={method}"
    body = [{"index": 0, "methodname": method, "args": args}]
    response = _request(session, "POST", url, json=body)
    try:
        item = response.json()[0]
    except (ValueError, IndexError, TypeError) as e:
        raise MoodleError(f"bad response from {method}: {response.text[:200]!r}") from e
    if item.get("error"):
        exc = item["exception"]
        errorcode = exc["errorcode"]
        message = exc["message"]
        if errorcode in SESSION_EXPIRED_CODES:
            raise SessionExpired(message, errorcode)
        raise MoodleError(message, errorcode)
    return item["data"]


def get_page(session, url: str) -> str:
    response = _request(session, "GET", url, follow_redirects=True)
    final = urlsplit(str(response.url))
    if final.hostname == "sso1.ut.ac.ir" or final.path == "/login/index.php":
        raise SessionExpired("login required", "requireloginerror")
    return response.text


def courses(session) -> list[dict]:
    data = call(
        session,
        "core_course_get_enrolled_courses_by_timeline_classification",
        {"classification": "all", "limit": 0, "offset": 0, "sort": "fullname"},
    )
    return [
        {"id": c["id"], "fullname": c["fullname"], "shortname": c["shortname"], "url": c["viewurl"]}
        for c in data["courses"]
    ]


def deadlines(session, since: int, limit: int = 50) -> list[dict]:
    data = call(
        session,
        "core_calendar_get_action_events_by_timesort",
        {"timesortfrom": since, "limitnum": limit},
    )
    events = []
    for ev in data["events"]:
        query = parse_qs(urlsplit(ev["url"]).query)
        cmid = int(query["id"][0]) if "id" in query else None
        events.append(
            {
                "event_id": ev["id"],
                "name": ev["name"],
                "module": ev["modulename"],
                "course_id": ev["course"]["id"],
                "course": ev["course"]["fullname"],
                "cmid": cmid,
                "url": ev["url"],
                "due": ev["timesort"],
                "overdue": ev["overdue"],
            }
        )
    return events


def download(session, url: str, dest_dir: pathlib.Path) -> pathlib.Path:
    if not url.startswith(f"{BASE}/pluginfile.php/"):
        raise ValueError(f"not a pluginfile url: {url}")
    if "forcedownload=1" not in url:
        url += ("&" if "?" in url else "?") + "forcedownload=1"
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"bad file name: {name!r}")
    response = _request(session, "GET", url, follow_redirects=True)
    if response.headers.get("content-type", "").startswith("text/html"):
        raise SessionExpired("login page instead of file", "requireloginerror")
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / name
    path.write_bytes(response.content)
    return path
