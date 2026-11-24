"""Grades, notifications and richer assignment info."""

import re

from bs4 import BeautifulSoup

from . import moodle

SUBMISSION_MARKER = "/assignsubmission_file/submission_files/"


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_grades(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="overview-grade")
    grades = []
    if table is None:
        return grades
    for row in table.find_all("tr"):
        if "emptyrow" in (row.get("class") or []):
            continue
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        link = cells[0].find("a", href=True)
        grade = _collapse(cells[1].get_text())
        grades.append(
            {
                "course": _collapse(cells[0].get_text()),
                "course_url": link["href"] if link else None,
                "grade": grade if grade != "-" else None,
            }
        )
    return grades


def grades(session) -> list[dict]:
    html = moodle.get_page(session, f"{moodle.BASE}/grade/report/overview/index.php")
    return parse_grades(html)


def notifications(session, limit: int = 20) -> list[dict]:
    data = moodle.call(
        session,
        "message_popup_get_popup_notifications",
        {"useridto": session.user_id, "newestfirst": 1, "limit": limit, "offset": 0},
    )
    return [
        {
            "id": n["id"],
            "subject": n["subject"],
            "text": n["smallmessage"],
            "url": n["contexturl"],
            "time": n["timecreated"],
            "read": n["read"],
            "type": n["eventtype"],
        }
        for n in data["notifications"]
    ]


def submitted_files(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    files = []
    seen = set()
    for a in soup.find_all("a", href=True):
        if SUBMISSION_MARKER in a["href"] and a["href"] not in seen:
            seen.add(a["href"])
            files.append({"name": a.get_text().strip(), "url": a["href"]})
    return files


def feedback(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    div = soup.find("div", class_="feedback")
    if div is None:
        return {"grade": None, "comments": None}
    rows = div.find_all("tr")
    grade = None
    comments = None
    if rows:
        td = rows[0].find("td")
        if td is not None:
            grade = _collapse(td.get_text()) or None
    # comments are rich text; plain rows like grade date and grader are not comments
    for row in rows:
        td = row.find("td")
        if td is not None and td.find("p") is not None:
            comments = _collapse(td.get_text()) or None
            break
    return {"grade": grade, "comments": comments}
