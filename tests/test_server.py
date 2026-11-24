import asyncio
import json
import os
import sys
import types

import pytest

import elearn_mcp
from elearn_mcp import (
    course,
    extract,
    info,
    materials,
    moodle,
    notify,
    parse,
    server,
    sso,
    submit,
    watch,
)

TOOL_NAMES = [
    "list_accounts",
    "list_courses",
    "list_deadlines",
    "all_deadlines",
    "course_contents",
    "download_module",
    "external_link",
    "grades",
    "notifications",
    "get_assignment",
    "download_attachments",
    "prepare_submission",
    "confirm_submission",
    "remove_submission",
    "download_course_materials",
    "list_materials",
    "read_material",
    "send_notification",
    "watch_now",
    "start_materials_job",
    "start_agents",
    "job_status",
    "start_watch_round",
]


def make_accounts():
    def fake_login(username, password, student_id=None, client=None):
        return sso.Session(client=object(), sesskey="k", user_id=1, student_id=student_id)

    def fake_list(username, password, client=None):
        return ["1001", "1002"]

    return server.Accounts("u", "p", login=fake_login, list_accounts=fake_list)


@pytest.fixture(autouse=True)
def fake_clone(monkeypatch):
    monkeypatch.setattr(sso, "clone_sso_cookies", lambda client: object(), raising=False)


@pytest.fixture
def accounts(monkeypatch):
    acc = make_accounts()
    monkeypatch.setattr(server, "_accounts", acc)
    return acc


def call(tool, **args):
    result = asyncio.run(server.mcp.call_tool(tool, args))
    assert result.is_error is False
    if result.structured_content is not None:
        return result.structured_content["result"]
    return json.loads(result.content[0].text)


def call_error(tool, **args):
    # _handle_call_tool turns a ToolError into CallToolResult(is_error, text=str(exc))
    from mcp.server.mcpserver.exceptions import ToolError

    try:
        asyncio.run(server.mcp.call_tool(tool, args))
    except ToolError as e:
        return str(e)
    raise AssertionError(f"{tool} did not raise a ToolError")


@pytest.fixture(autouse=True)
def clear_pending():
    server._pending.clear()


def test_tools_registered(accounts):
    names = [t.name for t in asyncio.run(server.mcp.list_tools())]
    assert names == TOOL_NAMES


def test_list_accounts(accounts):
    assert call("list_accounts") == ["1001", "1002"]


def test_list_courses(accounts, monkeypatch):
    seen = []

    def fake_courses(session):
        seen.append(session.student_id)
        return [{"id": 1, "fullname": "f", "shortname": "s", "url": "u"}]

    monkeypatch.setattr(moodle, "courses", fake_courses)
    result = call("list_courses", account="1001")
    assert result[0]["id"] == 1
    assert seen == ["1001"]


def test_list_deadlines_since(accounts, monkeypatch):
    import time

    seen = {}

    def fake_deadlines(session, since, limit=50):
        seen["since"] = since
        seen["limit"] = limit
        return []

    monkeypatch.setattr(moodle, "deadlines", fake_deadlines)
    before = int(time.time())
    call("list_deadlines", account="1001", days_back=2)
    after = int(time.time())
    assert seen["limit"] == 50
    assert before - 2 * 86400 <= seen["since"] <= after - 2 * 86400


def test_all_deadlines_merges_and_sorts(accounts, monkeypatch):
    events = {
        "1001": [{"name": "a", "due": 300}],
        "1002": [{"name": "b", "due": 100}, {"name": "c", "due": 200}],
    }

    def fake_deadlines(session, since, limit=50):
        return events[session.student_id]

    monkeypatch.setattr(moodle, "deadlines", fake_deadlines)
    result = call("all_deadlines")
    assert [e["name"] for e in result] == ["b", "c", "a"]
    assert [e["account"] for e in result] == ["1002", "1002", "1001"]


def test_get_assignment_builds_url(accounts, monkeypatch):
    seen = {}

    def fake_get_page(session, url):
        seen["url"] = url
        return "<html></html>"

    monkeypatch.setattr(moodle, "get_page", fake_get_page)
    monkeypatch.setattr(parse, "parse_assignment", lambda html: {"name": "hw"})
    result = call("get_assignment", account="1001", cmid=42)
    assert seen["url"] == "https://elearn.ut.ac.ir/mod/assign/view.php?id=42"
    assert result["name"] == "hw"
    assert result["account"] == "1001"
    assert result["url"] == "https://elearn.ut.ac.ir/mod/assign/view.php?id=42"


def test_download_attachments_uses_dir(accounts, monkeypatch, tmp_path):
    monkeypatch.setenv("ELEARN_DOWNLOAD_DIR", str(tmp_path))
    dest = tmp_path / "1001" / "42"
    (dest).mkdir(parents=True)
    target = dest / "file.pdf"
    target.write_bytes(b"x")

    file_url = "https://elearn.ut.ac.ir/pluginfile.php/1/mod_assign/introattachment/0/file.pdf"

    def fake_download(session, url, dest_dir):
        assert url == file_url
        assert dest_dir == dest
        return target

    monkeypatch.setattr(moodle, "download", fake_download)
    monkeypatch.setattr(
        parse,
        "parse_assignment",
        lambda html: {"attachments": [{"name": "file.pdf", "url": file_url}]},
    )
    monkeypatch.setattr(moodle, "get_page", lambda session, url: "<html></html>")
    result = call("download_attachments", account="1001", cmid=42)
    assert result == [str(target)]


def test_tool_relogs_in_on_session_expired(accounts, monkeypatch):
    calls = {"n": 0}

    def fake_courses(session):
        calls["n"] += 1
        if calls["n"] == 1:
            raise moodle.SessionExpired("expired", "requireloginerror")
        return [{"id": 1, "fullname": "f", "shortname": "s", "url": "u"}]

    monkeypatch.setattr(moodle, "courses", fake_courses)
    result = call("list_courses", account="1001")
    assert result[0]["id"] == 1
    assert calls["n"] == 2


def test_course_contents(accounts, monkeypatch):
    seen = {}

    def fake_contents(session, course_id):
        seen["course_id"] = course_id
        seen["student"] = session.student_id
        return [{"number": 0, "title": "t", "visible": True, "items": []}]

    monkeypatch.setattr(course, "contents", fake_contents)
    result = call("course_contents", account="1001", course_id=7)
    assert seen == {"course_id": 7, "student": "1001"}
    assert result[0]["title"] == "t"


def test_download_module_uses_dir(accounts, monkeypatch, tmp_path):
    monkeypatch.setenv("ELEARN_DOWNLOAD_DIR", str(tmp_path))
    dest = tmp_path / "1001" / "55"
    target = dest / "a.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    seen = {}

    def fake_download_module(session, cmid, module, dest_dir):
        seen["cmid"] = cmid
        seen["module"] = module
        seen["dest"] = dest_dir
        return [target]

    monkeypatch.setattr(course, "download_module", fake_download_module)
    result = call("download_module", account="1001", cmid=55, module="resource")
    assert result == [str(target)]
    assert seen["cmid"] == 55
    assert seen["module"] == "resource"
    assert seen["dest"] == dest


def test_external_link(accounts, monkeypatch):
    seen = {}

    def fake_get_page(session, url):
        seen["url"] = url
        return "<html></html>"

    monkeypatch.setattr(moodle, "get_page", fake_get_page)
    monkeypatch.setattr(course, "external_url", lambda html: "https://ext.example.com/x")
    result = call("external_link", account="1001", cmid=9)
    assert seen["url"] == "https://elearn.ut.ac.ir/mod/url/view.php?id=9"
    assert result == "https://ext.example.com/x"


def test_grades(accounts, monkeypatch):
    seen = {}

    def fake_grades(session):
        seen["student"] = session.student_id
        return [{"course": "c", "course_url": "u", "grade": "18"}]

    monkeypatch.setattr(info, "grades", fake_grades)
    result = call("grades", account="1001")
    assert seen["student"] == "1001"
    assert result[0]["grade"] == "18"


def test_notifications(accounts, monkeypatch):
    seen = {}

    def fake_notifications(session, limit=20):
        seen["limit"] = limit
        seen["user_id"] = session.user_id
        return [
            {"id": 1, "subject": "s", "text": "t", "url": "u", "time": 1, "read": 0, "type": "x"}
        ]

    monkeypatch.setattr(info, "notifications", fake_notifications)
    result = call("notifications", account="1001", limit=5)
    assert seen["limit"] == 5
    assert seen["user_id"] == 1
    assert result[0]["subject"] == "s"


def test_get_assignment_includes_files_and_feedback(accounts, monkeypatch):
    monkeypatch.setattr(moodle, "get_page", lambda session, url: "<html></html>")
    monkeypatch.setattr(
        parse, "parse_assignment", lambda html: {"name": "hw", "status": {"Status": "draft"}}
    )
    monkeypatch.setattr(info, "submitted_files", lambda html: [{"name": "a.pdf", "url": "u"}])
    monkeypatch.setattr(info, "feedback", lambda html: {"grade": "20", "comments": "ok"})
    result = call("get_assignment", account="1001", cmid=42)
    assert result["submitted_files"] == [{"name": "a.pdf", "url": "u"}]
    assert result["feedback"] == {"grade": "20", "comments": "ok"}
    assert result["status"] == {"Status": "draft"}


def test_prepare_submission_missing_file_stores_nothing(accounts, monkeypatch):
    monkeypatch.setattr(moodle, "get_page", lambda session, url: "<html></html>")
    monkeypatch.setattr(
        parse, "parse_assignment", lambda html: {"name": "hw", "status": {"Status": "draft"}}
    )
    monkeypatch.setattr(submit, "parse_edit_form", lambda html: {"maxbytes": 100, "maxfiles": 5})
    text = call_error("prepare_submission", account="1001", cmid=42, paths=["/nope.pdf"])
    assert "file not found" in text
    assert server._pending == {}


def test_prepare_submission_too_big(accounts, monkeypatch, tmp_path):
    f = tmp_path / "big.pdf"
    f.write_bytes(b"x" * 200)
    monkeypatch.setattr(moodle, "get_page", lambda session, url: "<html></html>")
    monkeypatch.setattr(
        parse, "parse_assignment", lambda html: {"name": "hw", "status": {"Status": "draft"}}
    )
    monkeypatch.setattr(submit, "parse_edit_form", lambda html: {"maxbytes": 100, "maxfiles": 5})
    text = call_error("prepare_submission", account="1001", cmid=42, paths=[str(f)])
    assert "file too big" in text
    assert server._pending == {}


def test_prepare_submission_good_case(accounts, monkeypatch, tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"12345")
    monkeypatch.setattr(moodle, "get_page", lambda session, url: "<html></html>")
    monkeypatch.setattr(
        parse, "parse_assignment", lambda html: {"name": "hw", "status": {"Status": "draft"}}
    )
    monkeypatch.setattr(submit, "parse_edit_form", lambda html: {"maxbytes": 100, "maxfiles": 5})
    uploads = []
    monkeypatch.setattr(
        submit, "upload_files", lambda s, cmid, paths: uploads.append((cmid, paths))
    )
    result = call("prepare_submission", account="1001", cmid=42, paths=[str(f)])
    assert result["account"] == "1001"
    assert result["cmid"] == 42
    assert result["assignment"] == "hw"
    assert result["files"] == [{"name": "a.pdf", "bytes": 5}]
    assert result["current_status"] == {"Status": "draft"}
    assert result["expires_in_s"] == 600
    assert "confirm_submission" in result["note"]
    assert uploads == []
    assert list(server._pending) == [result["token"]]


def test_confirm_submission_uploads_once(accounts, monkeypatch, tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"12345")
    monkeypatch.setattr(moodle, "get_page", lambda session, url: "<html></html>")
    monkeypatch.setattr(
        parse, "parse_assignment", lambda html: {"name": "hw", "status": {"Status": "submitted"}}
    )
    monkeypatch.setattr(submit, "parse_edit_form", lambda html: {"maxbytes": 100, "maxfiles": 5})
    uploads = []
    monkeypatch.setattr(
        submit,
        "upload_files",
        lambda s, cmid, paths: (
            uploads.append((s.student_id, cmid, list(paths))) or {"uploaded": ["a.pdf"]}
        ),
    )
    monkeypatch.setattr(info, "submitted_files", lambda html: [{"name": "a.pdf", "url": "u"}])
    token = call("prepare_submission", account="1001", cmid=42, paths=[str(f)])["token"]
    result = call("confirm_submission", token=token)
    assert result["uploaded"] == ["a.pdf"]
    assert result["status"] == {"Status": "submitted"}
    assert result["submitted_files"] == [{"name": "a.pdf", "url": "u"}]
    assert uploads == [("1001", 42, [str(f)])]
    assert server._pending == {}
    text = call_error("confirm_submission", token=token)
    assert "unknown or expired token" in text


def test_confirm_submission_expired_token(accounts, monkeypatch):
    server._pending["tok"] = {"account": "1001", "cmid": 42, "paths": [], "created": 0}
    monkeypatch.setattr(server.time, "monotonic", lambda: 1000)
    text = call_error("confirm_submission", token="tok")
    assert "unknown or expired token" in text


def test_confirm_submission_unknown_token(accounts):
    text = call_error("confirm_submission", token="nope")
    assert "unknown or expired token" in text


def test_remove_submission_requires_confirm(accounts, monkeypatch):
    removed = []
    monkeypatch.setattr(submit, "remove_submission", lambda s, cmid: removed.append(cmid))
    text = call_error("remove_submission", account="1001", cmid=42)
    assert "confirm=true" in text
    assert removed == []
    monkeypatch.setattr(moodle, "get_page", lambda session, url: "<html></html>")
    monkeypatch.setattr(
        parse, "parse_assignment", lambda html: {"status": {"Status": "not submitted"}}
    )
    result = call("remove_submission", account="1001", cmid=42, confirm=True)
    assert removed == [42]
    assert result == {"Status": "not submitted"}


def test_tool_error_is_readable(accounts, monkeypatch):
    def boom(session):
        raise ValueError("unknown student id: 123")

    monkeypatch.setattr(moodle, "courses", boom)
    text = call_error("list_courses", account="1001")
    assert "unknown student id: 123" in text


def test_download_course_materials_uses_materials_dir(accounts, monkeypatch, tmp_path):
    monkeypatch.setenv("ELEARN_MATERIALS_DIR", str(tmp_path))
    monkeypatch.setattr(server, "MATERIALS_DIR", tmp_path)
    seen = {}

    def fake_download(session, course_id, dest_dir, modules=None, extract=True):
        seen["course_id"] = course_id
        seen["dest"] = dest_dir
        seen["extract"] = extract
        return {
            "downloaded": 2,
            "skipped": 1,
            "errors": [],
            "files": [{"path": "a"}, {"path": "b"}],
        }

    monkeypatch.setattr(materials, "download_course", fake_download)
    result = call("download_course_materials", account="1001", course_id=7, extract=False)
    assert seen["course_id"] == 7
    assert seen["dest"] == tmp_path / "1001" / "7"
    assert seen["extract"] is False
    assert result == {
        "dir": str(tmp_path / "1001" / "7"),
        "downloaded": 2,
        "skipped": 1,
        "errors": [],
        "files": 2,
    }


def test_list_materials_returns_manifest_files(accounts, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "MATERIALS_DIR", tmp_path)
    files = [
        {
            "section": "s",
            "item": "i",
            "path": "01 s/a.pdf",
            "bytes": 3,
            "text_path": "01 s/a.pdf.txt",
        }
    ]
    monkeypatch.setattr(materials, "list_materials", lambda dest: {"files": files, "errors": []})
    result = call("list_materials", account="1001", course_id=7)
    assert result == files


def test_read_material_paging_and_txt_copy(accounts, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "MATERIALS_DIR", tmp_path)
    dest = tmp_path / "1001" / "7"
    (dest / "01 s").mkdir(parents=True)
    (dest / "01 s" / "a.pdf").write_bytes(b"binary")
    (dest / "01 s" / "a.pdf.txt").write_text("0123456789", encoding="utf-8")
    monkeypatch.setattr(
        materials,
        "list_materials",
        lambda d: {
            "files": [
                {
                    "path": "01 s/a.pdf",
                    "text_path": "01 s/a.pdf.txt",
                    "text_version": server.extract.VERSION,
                }
            ],
            "errors": [],
        },
    )
    result = call("read_material", account="1001", course_id=7, path="01 s/a.pdf", max_chars=4)
    assert result == {"path": "01 s/a.pdf", "text": "0123", "total_chars": 10, "next_offset": 4}
    result = call(
        "read_material", account="1001", course_id=7, path="01 s/a.pdf", offset=4, max_chars=4
    )
    assert result["text"] == "4567"
    assert result["next_offset"] == 8
    result = call("read_material", account="1001", course_id=7, path="01 s/a.pdf", offset=8)
    assert result["text"] == "89"
    assert result["next_offset"] is None


def test_read_material_falls_back_to_extraction(accounts, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "MATERIALS_DIR", tmp_path)
    dest = tmp_path / "1001" / "7"
    dest.mkdir(parents=True)
    (dest / "note.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(materials, "list_materials", lambda d: {"files": [], "errors": []})
    extracted = []
    monkeypatch.setattr(extract, "extract_text", lambda p: extracted.append(p) or "hello")
    result = call("read_material", account="1001", course_id=7, path="note.txt")
    assert result["text"] == "hello"
    assert extracted == [dest / "note.txt"]


def test_read_material_refuses_paths_outside_course_dir(accounts, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "MATERIALS_DIR", tmp_path)
    (tmp_path / "1001" / "7").mkdir(parents=True)
    text = call_error("read_material", account="1001", course_id=7, path="../x")
    assert "path outside course materials" in text
    text = call_error("read_material", account="1001", course_id=7, path="/etc/passwd")
    assert "path outside course materials" in text


def test_read_material_caps_max_chars(accounts, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "MATERIALS_DIR", tmp_path)
    dest = tmp_path / "1001" / "7"
    dest.mkdir(parents=True)
    (dest / "big.txt").write_text("x" * 60000, encoding="utf-8")
    monkeypatch.setattr(materials, "list_materials", lambda d: {"files": [], "errors": []})
    result = call("read_material", account="1001", course_id=7, path="big.txt", max_chars=999999)
    assert len(result["text"]) == 50000
    assert result["next_offset"] == 50000


def test_send_notification_defaults_to_macos(accounts, monkeypatch):
    seen = {}

    def fake_send(text, channels, title="elearn"):
        seen["text"] = text
        seen["channels"] = channels
        return {"macos": "ok"}

    monkeypatch.setattr(notify, "send", fake_send)
    result = call("send_notification", text="hi")
    assert seen == {"text": "hi", "channels": ["macos"]}
    assert result == {"macos": "ok"}
    result = call("send_notification", text="hi", channels=["telegram"])
    assert seen["channels"] == ["telegram"]


def test_watch_now_runs_one_round(accounts, monkeypatch):
    seen = {}

    def fake_run_once(acc, state_path, channels):
        seen["acc"] = acc
        seen["state_path"] = state_path
        seen["channels"] = channels
        return [{"kind": "due_soon", "text": "t", "account": "1001"}]

    monkeypatch.setattr(watch, "run_once", fake_run_once)
    result = call("watch_now")
    assert seen["acc"] is accounts
    assert seen["state_path"] == watch.STATE_PATH
    assert seen["channels"] == ["macos"]
    assert result == [{"kind": "due_soon", "text": "t", "account": "1001"}]


def test_confirm_submission_refuses_changed_file(accounts, monkeypatch, tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"12345")
    monkeypatch.setattr(moodle, "get_page", lambda session, url: "<html></html>")
    monkeypatch.setattr(
        parse, "parse_assignment", lambda html: {"name": "hw", "status": {"Status": "draft"}}
    )
    monkeypatch.setattr(submit, "parse_edit_form", lambda html: {"maxbytes": 100, "maxfiles": 5})
    uploads = []
    monkeypatch.setattr(submit, "upload_files", lambda s, cmid, paths: uploads.append(paths))
    token = call("prepare_submission", account="1001", cmid=42, paths=[str(f)])["token"]
    f.write_bytes(b"CHANGED")
    text = call_error("confirm_submission", token=token)
    assert "file changed since prepare_submission: a.pdf" in text
    assert uploads == []
    assert server._pending == {}


def test_confirm_submission_refuses_missing_file(accounts, monkeypatch, tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"12345")
    monkeypatch.setattr(moodle, "get_page", lambda session, url: "<html></html>")
    monkeypatch.setattr(
        parse, "parse_assignment", lambda html: {"name": "hw", "status": {"Status": "draft"}}
    )
    monkeypatch.setattr(submit, "parse_edit_form", lambda html: {"maxbytes": 100, "maxfiles": 5})
    uploads = []
    monkeypatch.setattr(submit, "upload_files", lambda s, cmid, paths: uploads.append(paths))
    token = call("prepare_submission", account="1001", cmid=42, paths=[str(f)])["token"]
    f.unlink()
    text = call_error("confirm_submission", token=token)
    assert "file changed since prepare_submission: a.pdf" in text
    assert uploads == []


def test_material_tools_refuse_unknown_account(accounts, monkeypatch, tmp_path):
    from mcp.server.mcpserver.exceptions import ToolError

    monkeypatch.setattr(server, "MATERIALS_DIR", tmp_path / "materials")
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    for tool, args in [
        ("list_materials", {"account": "..", "course_id": 1}),
        ("read_material", {"account": "..", "course_id": 1, "path": "../secret.txt"}),
        ("download_course_materials", {"account": "../x", "course_id": 1}),
    ]:
        with pytest.raises(ToolError, match="unknown student id"):
            call(tool, **args)


def _manifest_with_copy(dest, version):
    entry = {"path": "01 s/a.txt", "text_path": "01 s/a.txt.txt", "text_version": version}
    (dest / "manifest.json").write_text(
        json.dumps({"files": [entry], "errors": []}), encoding="utf-8"
    )


def test_read_material_rebuilds_stale_or_missing_copy(accounts, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "MATERIALS_DIR", tmp_path)
    dest = tmp_path / "1001" / "7"
    (dest / "01 s").mkdir(parents=True)
    (dest / "01 s" / "a.txt").write_text("fresh text", encoding="utf-8")
    (dest / "01 s" / "a.txt.txt").write_text("old text", encoding="utf-8")
    _manifest_with_copy(dest, 1)  # copy made by an older extractor
    result = call("read_material", account="1001", course_id=7, path="01 s/a.txt")
    assert result["text"] == "fresh text"
    saved = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert saved["files"][0]["text_version"] == server.extract.VERSION
    (dest / "01 s" / "a.txt.txt").unlink()  # copy deleted but still in the manifest
    result = call("read_material", account="1001", course_id=7, path="01 s/a.txt")
    assert result["text"] == "fresh text"


@pytest.fixture
def fake_jobs(monkeypatch):
    fake = types.ModuleType("elearn_mcp.jobs")

    class JobsUnavailable(Exception):
        pass

    fake.JobsUnavailable = JobsUnavailable
    fake.materials_job = object()
    fake.watch_round = object()
    fake.start = lambda task, *args, **kwargs: "job-123"
    fake.fan_out = lambda parts, model: types.SimpleNamespace(id="chord-456")
    fake.job_info = lambda job_id: {"id": job_id, "state": "SUCCESS", "result": {"ok": True}}
    monkeypatch.setitem(sys.modules, "elearn_mcp.jobs", fake)
    # the package attribute wins over sys.modules once the real module was imported
    monkeypatch.setattr(elearn_mcp, "jobs", fake, raising=False)
    return fake


def test_start_materials_job_unknown_account(accounts, fake_jobs, monkeypatch):
    monkeypatch.setattr(fake_jobs, "start", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    text = call_error("start_materials_job", account="nope", course_id=7)
    assert "unknown student id" in text


def test_start_materials_job_returns_job_id(accounts, fake_jobs, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "MATERIALS_DIR", tmp_path)
    seen = {}

    def fake_start(task, *args, **kwargs):
        seen["task"] = task
        seen["args"] = args
        return "job-123"

    monkeypatch.setattr(fake_jobs, "start", fake_start)
    result = call("start_materials_job", account="1001", course_id=7, extract=False)
    assert result == {"job_id": "job-123"}
    assert seen["task"] is fake_jobs.materials_job
    assert seen["args"] == ("1001", 7, False)


def test_start_agents_missing_prompt(accounts, fake_jobs):
    parts = [{"prompt": "a", "workdir": "/w"}, {"workdir": "/w"}]
    text = call_error("start_agents", parts=parts)
    assert "1" in text
    assert "prompt" in text


def test_start_agents_too_many_parts(accounts, fake_jobs):
    parts = [{"prompt": f"p{i}", "workdir": "/w"} for i in range(7)]
    text = call_error("start_agents", parts=parts)
    assert "6" in text


def test_start_agents_good_case(accounts, fake_jobs, monkeypatch):
    seen = {}

    def fake_fan_out(parts, model):
        seen["parts"] = parts
        seen["model"] = model
        return types.SimpleNamespace(id="chord-456")

    monkeypatch.setattr(fake_jobs, "fan_out", fake_fan_out)
    parts = [{"prompt": "a", "workdir": "/w1"}, {"prompt": "b", "workdir": "/w2"}]
    result = call("start_agents", parts=parts, model="free-model")
    assert result == {"job_id": "chord-456", "parts": 2}
    assert seen["parts"] == parts
    assert seen["model"] == "free-model"


def test_job_status_returns_job_info(accounts, fake_jobs):
    result = call("job_status", job_id="job-123")
    assert result == {"id": "job-123", "state": "SUCCESS", "result": {"ok": True}}


def test_start_watch_round_returns_job_id(accounts, fake_jobs, monkeypatch):
    seen = {}

    def fake_start(task, *args, **kwargs):
        seen["task"] = task
        seen["args"] = args
        return "job-789"

    monkeypatch.setattr(fake_jobs, "start", fake_start)
    result = call("start_watch_round")
    assert result == {"job_id": "job-789"}
    assert seen["task"] is fake_jobs.watch_round
    assert seen["args"] == (["macos"],)
    result = call("start_watch_round", channels=["telegram"])
    assert seen["args"] == (["telegram"],)


def test_jobs_unavailable_reaches_client(accounts, fake_jobs, monkeypatch):
    def boom(*args, **kwargs):
        raise fake_jobs.JobsUnavailable("redis is not running: brew services start redis")

    monkeypatch.setattr(fake_jobs, "start", boom)
    text = call_error("start_materials_job", account="1001", course_id=7)
    assert "redis is not running" in text
    monkeypatch.setattr(fake_jobs, "fan_out", boom)
    text = call_error("start_agents", parts=[{"prompt": "a", "workdir": "/w"}])
    assert "redis is not running" in text
    monkeypatch.setattr(fake_jobs, "job_info", boom)
    text = call_error("job_status", job_id="job-123")
    assert "redis is not running" in text


def test_data_dirs_default_to_absolute_paths():
    assert server.MATERIALS_DIR.is_absolute() or "ELEARN_MATERIALS_DIR" in os.environ
    assert server.DATA_DIR.is_absolute()
