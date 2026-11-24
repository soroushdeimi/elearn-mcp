"""Course contents and resource downloads."""

import json
import pathlib

from bs4 import BeautifulSoup

from . import moodle
from .moodle import BASE, MoodleError, SessionExpired

FOLDER_MARKER = "/mod_folder/content/"


def contents(session, course_id: int) -> list[dict]:
    data = moodle.call(session, "core_courseformat_get_state", {"courseid": course_id})
    state = json.loads(data)
    cms = {cm["id"]: cm for cm in state["cm"]}
    sections = []
    for section in state["section"]:
        items = []
        for cmid in section["cmlist"]:
            cm = cms.get(cmid)
            if cm is None:
                continue
            items.append(
                {
                    "cmid": int(cm["id"]),
                    "name": cm["name"],
                    "module": cm["module"],
                    "url": cm.get("url"),  # labels have no url
                    "visible": cm["uservisible"],
                }
            )
        sections.append(
            {
                "number": section["number"],
                "title": section["title"],
                "visible": section["visible"],
                "items": items,
            }
        )
    return sections


def resource_url(session, cmid: int) -> str:
    url = f"{BASE}/mod/resource/view.php?id={cmid}&redirect=1"
    response = moodle._request(session, "GET", url, follow_redirects=False)
    if 300 <= response.status_code < 400:
        location = response.headers.get("location", "")
        if location.startswith(f"{BASE}/pluginfile.php/"):
            return location
        if "sso1.ut.ac.ir" in location or "/login/index.php" in location:
            raise SessionExpired("login required", "requireloginerror")
    raise MoodleError("not a downloadable resource")


def folder_files(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    files = []
    seen = set()
    for a in soup.find_all("a", href=True):
        if FOLDER_MARKER not in a["href"] or a["href"] in seen:
            continue
        seen.add(a["href"])
        files.append({"name": a.get_text().strip(), "url": a["href"]})
    return files


def external_url(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    div = soup.find("div", class_="urlworkaround")
    if div is None:
        return None
    a = div.find("a", href=True)
    return a["href"] if a else None


def download_module(session, cmid: int, module: str, dest_dir: pathlib.Path) -> list[pathlib.Path]:
    if module == "resource":
        return [moodle.download(session, resource_url(session, cmid), dest_dir)]
    if module == "folder":
        html = moodle.get_page(session, f"{BASE}/mod/folder/view.php?id={cmid}")
        return [moodle.download(session, f["url"], dest_dir) for f in folder_files(html)]
    raise ValueError(f"unsupported module: {module}")
