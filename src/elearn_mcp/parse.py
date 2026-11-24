"""Parse the /mod/assign/view.php page."""

import re

from bs4 import BeautifulSoup

ATTACHMENT_MARKER = "/mod_assign/introattachment/"


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_assignment(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    info = soup.find(attrs={"data-region": "activity-information"})
    name = info.get("data-activityname") if info else None

    dates = {}
    for item in soup.select(".activity-dates .date-item"):
        label = item.select_one(".date-item-label")
        if label is None:
            continue
        key = label.get_text().rstrip(":")
        label.extract()
        dates[key] = _collapse(item.get_text())

    attachments = [
        {"name": a.get_text().strip(), "url": a["href"]}
        for a in soup.find_all("a", href=True)
        if ATTACHMENT_MARKER in a["href"]
    ]

    description = ""
    desc = soup.select_one(".activity-description")
    if desc is not None:
        for tree in desc.select("div[id^=assign_files_tree]"):
            tree.decompose()
        description = _collapse(desc.get_text())

    status = {}
    table = soup.select_one(".submissionstatustable table")
    if table is not None:
        for row in table.find_all("tr"):
            th, td = row.find("th"), row.find("td")
            if th is not None and td is not None:
                status[_collapse(th.get_text())] = _collapse(td.get_text())

    form = None
    for f in soup.find_all("form"):
        if f.find("input", attrs={"name": "action", "value": "editsubmission"}):
            form = f
            break
    can_submit = form is not None
    cmid = None
    if form is not None:
        id_input = form.find("input", attrs={"name": "id"})
        if id_input is not None and id_input.get("value"):
            cmid = int(id_input["value"])

    return {
        "name": name,
        "dates": dates,
        "description": description,
        "attachments": attachments,
        "status": status,
        "can_submit": can_submit,
        "cmid": cmid,
    }
