"""Assignment submission: file upload and removal."""

import pathlib
import re

from bs4 import BeautifulSoup

from . import moodle

BASE = moodle.BASE


class SubmitError(Exception):
    pass


def parse_edit_form(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", class_="mform")
    if form is None:
        raise SubmitError("submission form not found")
    fields = {
        i["name"]: i.get("value", "") for i in form.find_all("input", attrs={"type": "hidden"})
    }
    client_id = re.search(r'"client_id":"([^"]+)"', html)
    ctx_id = re.search(r'"context":\{"id":(\d+)', html)
    maxbytes = re.search(r'"maxbytes":"?(-?\d+)"?', html)
    maxfiles = re.search(r'"maxfiles":"?(-?\d+)"?', html)
    repo = re.search(r'"(\d+)":\{"id":"\1","name":"[^"]*","type":"upload"', html)
    if not (client_id and ctx_id and maxbytes and maxfiles):
        raise SubmitError("submission form not found")
    if repo is None:
        raise SubmitError("upload repository not found")
    return {
        "fields": fields,
        "itemid": fields["files_filemanager"],
        "client_id": client_id.group(1),
        "ctx_id": ctx_id.group(1),
        "repo_id": repo.group(1),
        "maxbytes": int(maxbytes.group(1)),
        "maxfiles": int(maxfiles.group(1)),
    }


def upload_files(session, cmid, paths) -> dict:
    """Upload files to the assignment and save the submission.

    Files already in the draft area are kept (Moodle retains the previous
    submission's files); this only adds the given files and saves.
    """
    html = moodle.get_page(session, f"{BASE}/mod/assign/view.php?id={cmid}&action=editsubmission")
    info = parse_edit_form(html)
    paths = [pathlib.Path(p) for p in paths]
    for p in paths:
        if not p.exists():
            raise SubmitError(f"file not found: {p.name}")
        if not p.is_file():
            raise SubmitError(f"not a file: {p.name}")
        if p.stat().st_size > info["maxbytes"]:
            raise SubmitError(f"file too big: {p.name}")
    if info["maxfiles"] != -1 and len(paths) > info["maxfiles"]:
        raise SubmitError(f"too many files: {len(paths)} > {info['maxfiles']}")
    uploaded = []
    for p in paths:
        with p.open("rb") as f:
            response = moodle._request(
                session,
                "POST",
                f"{BASE}/repository/repository_ajax.php?action=upload",
                data={
                    "title": p.name,
                    "author": "",
                    "license": "unknown",
                    "itemid": info["itemid"],
                    "repo_id": info["repo_id"],
                    "p": "",
                    "page": "",
                    "env": "filemanager",
                    "sesskey": session.sesskey,
                    "client_id": info["client_id"],
                    "maxbytes": str(info["maxbytes"]),
                    "areamaxbytes": "-1",
                    "ctx_id": info["ctx_id"],
                    "savepath": "/",
                },
                files={"repo_upload_file": (p.name, f)},
            )
        try:
            body = response.json()
        except ValueError as e:
            raise SubmitError(f"upload failed: {response.text[:200]}") from e
        if "error" in body:
            raise SubmitError(str(body["error"]))
        uploaded.append(p.name)
    final = moodle._request(
        session,
        "POST",
        f"{BASE}/mod/assign/view.php",
        data={**info["fields"], "submitbutton": "ذخیره تغییرات"},
        follow_redirects=True,
    )
    if "action=editsubmission" in str(final.url) or BeautifulSoup(final.text, "html.parser").find(
        "form", class_="mform"
    ):
        raise SubmitError("save failed, the form was shown again")
    return {"uploaded": uploaded, "final_url": str(final.url)}


def remove_submission(session, cmid) -> None:
    html = moodle.get_page(
        session, f"{BASE}/mod/assign/view.php?id={cmid}&action=removesubmissionconfirm"
    )
    soup = BeautifulSoup(html, "html.parser")
    for form in soup.find_all("form"):
        if form.find("input", attrs={"name": "action", "value": "removesubmission"}) is None:
            continue
        data = {i["name"]: i.get("value", "") for i in form.find_all("input") if i.get("name")}
        moodle._request(
            session,
            "POST",
            form.get("action") or f"{BASE}/mod/assign/view.php",
            data=data,
            follow_redirects=True,
        )
        return
    raise SubmitError("nothing to remove or removal not allowed")
