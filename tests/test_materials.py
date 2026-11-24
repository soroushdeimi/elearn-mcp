import types

import httpx
import pytest

from elearn_mcp import materials, moodle

SECTIONS = [
    {
        "number": 0,
        "title": "Week 1: intro",
        "visible": True,
        "items": [
            {"cmid": 1, "name": "notes.txt", "module": "resource", "url": "u1", "visible": True},
            {"cmid": 2, "name": "hidden.txt", "module": "resource", "url": "u2", "visible": False},
            {"cmid": 3, "name": "forum", "module": "forum", "url": "u3", "visible": True},
        ],
    },
    {
        "number": 1,
        "title": "Week 2",
        "visible": True,
        "items": [
            {
                "cmid": 4,
                "name": "weird/na:me...",
                "module": "resource",
                "url": "u4",
                "visible": True,
            },
            {"cmid": 5, "name": "slides", "module": "folder", "url": "u5", "visible": True},
        ],
    },
]
NAMES = {1: "notes.txt", 4: "weird/na:me...", 5: "slides"}


def make_session():
    return types.SimpleNamespace(client=httpx.Client(), sesskey="TESTSESSKEY", user_id=12345)


def patch_course(monkeypatch, sections=SECTIONS, fail_cmid=None, fail_exc=None):
    monkeypatch.setattr(materials.course, "contents", lambda session, course_id: sections)
    calls = []

    def fake_download_module(session, cmid, module, dest_dir):
        calls.append(cmid)
        if fail_cmid is not None and cmid == fail_cmid:
            raise fail_exc if fail_exc is not None else moodle.MoodleError("boom")
        dest_dir.mkdir(parents=True, exist_ok=True)
        if module == "resource":
            p = dest_dir / materials.safe(NAMES[cmid])
            p.write_bytes(b"DATA")
            return [p]
        p = dest_dir / "slide1.txt"
        p.write_bytes(b"SLIDES")
        return [p]

    monkeypatch.setattr(materials.course, "download_module", fake_download_module)
    return calls


def test_files_land_in_section_and_folder_paths_with_safe_names(tmp_path, monkeypatch):
    calls = patch_course(monkeypatch)
    result = materials.download_course(make_session(), 42, tmp_path)
    assert (tmp_path / "00 Week 1_ intro" / "notes.txt").read_bytes() == b"DATA"
    assert (tmp_path / "01 Week 2" / "weird_na_me").read_bytes() == b"DATA"
    assert (tmp_path / "01 Week 2" / "slides" / "slide1.txt").read_bytes() == b"SLIDES"
    assert calls == [1, 4, 5]  # hidden item 2 and forum item 3 skipped
    assert result["downloaded"] == 3
    assert result["skipped"] == 0


def test_manifest_written_with_relative_paths(tmp_path, monkeypatch):
    patch_course(monkeypatch)
    result = materials.download_course(make_session(), 42, tmp_path)
    assert result["course_id"] == 42
    by_cmid = {f["cmid"]: f for f in result["files"]}
    assert by_cmid[1]["path"] == "00 Week 1_ intro/notes.txt"
    assert by_cmid[1]["text_path"] == "00 Week 1_ intro/notes.txt.txt"
    assert by_cmid[1]["section"] == "Week 1: intro"
    assert by_cmid[1]["url"] == "u1"
    assert by_cmid[1]["bytes"] == 4
    assert by_cmid[5]["path"] == "01 Week 2/slides/slide1.txt"
    assert (tmp_path / "manifest.json").exists()
    assert result["errors"] == []


def test_second_run_skips_existing_files(tmp_path, monkeypatch):
    patch_course(monkeypatch)
    materials.download_course(make_session(), 42, tmp_path)
    calls = patch_course(monkeypatch)
    result = materials.download_course(make_session(), 42, tmp_path)
    assert calls == []
    assert result["downloaded"] == 0
    assert result["skipped"] == 3


def test_missing_file_is_redownloaded(tmp_path, monkeypatch):
    patch_course(monkeypatch)
    materials.download_course(make_session(), 42, tmp_path)
    (tmp_path / "00 Week 1_ intro" / "notes.txt").unlink()
    calls = patch_course(monkeypatch)
    result = materials.download_course(make_session(), 42, tmp_path)
    assert calls == [1]
    assert result["downloaded"] == 1
    assert result["skipped"] == 2
    assert [f["cmid"] for f in result["files"]] == [4, 5, 1]


def test_one_failing_item_recorded_and_others_continue(tmp_path, monkeypatch):
    patch_course(monkeypatch, fail_cmid=4)
    result = materials.download_course(make_session(), 42, tmp_path)
    assert result["downloaded"] == 2
    assert result["errors"] == [{"cmid": 4, "item": "weird/na:me...", "error": "boom"}]
    assert (tmp_path / "01 Week 2" / "slides" / "slide1.txt").exists()


def test_extract_false_writes_no_text_copies(tmp_path, monkeypatch):
    patch_course(monkeypatch)
    result = materials.download_course(make_session(), 42, tmp_path, extract=False)
    assert all(f["text_path"] is None for f in result["files"])
    assert not (tmp_path / "00 Week 1_ intro" / "notes.txt.txt").exists()


def test_extraction_error_recorded(tmp_path, monkeypatch):
    sections = [
        {
            "number": 0,
            "title": "S",
            "visible": True,
            "items": [
                {"cmid": 9, "name": "bad.pdf", "module": "resource", "url": "u9", "visible": True},
            ],
        }
    ]
    NAMES[9] = "bad.pdf"
    monkeypatch.setattr(materials.course, "contents", lambda session, course_id: sections)
    calls = []

    def fake_download_module(session, cmid, module, dest_dir):
        calls.append(cmid)
        dest_dir.mkdir(parents=True, exist_ok=True)
        p = dest_dir / "bad.pdf"
        p.write_bytes(b"NOT A PDF")
        return [p]

    monkeypatch.setattr(materials.course, "download_module", fake_download_module)
    result = materials.download_course(make_session(), 42, tmp_path)
    assert result["downloaded"] == 1
    assert result["files"][0]["text_path"] is None
    assert len(result["errors"]) == 1
    assert result["errors"][0]["cmid"] == 9
    assert "bad.pdf" in result["errors"][0]["item"]


def test_session_expired_reraises_and_keeps_progress(tmp_path, monkeypatch):
    patch_course(monkeypatch, fail_cmid=4, fail_exc=moodle.SessionExpired("login required"))
    with pytest.raises(moodle.SessionExpired):
        materials.download_course(make_session(), 42, tmp_path)
    on_disk = materials.list_materials(tmp_path)
    assert [f["cmid"] for f in on_disk["files"]] == [1]
    assert not (tmp_path / "01 Week 2" / "slides").exists()
    patch_course(monkeypatch)
    result = materials.download_course(make_session(), 42, tmp_path)
    assert result["skipped"] == 1
    assert result["downloaded"] == 2
    assert [f["cmid"] for f in result["files"]] == [1, 4, 5]


def test_session_expired_mid_folder_leaves_no_partial_manifest_entry(tmp_path, monkeypatch):
    sections = [
        {
            "number": 0,
            "title": "S",
            "visible": True,
            "items": [
                {
                    "cmid": 1,
                    "name": "notes.txt",
                    "module": "resource",
                    "url": "u1",
                    "visible": True,
                },
                {"cmid": 5, "name": "slides", "module": "folder", "url": "u5", "visible": True},
            ],
        }
    ]
    monkeypatch.setattr(materials.course, "contents", lambda session, course_id: sections)

    def failing_download_module(session, cmid, module, dest_dir):
        # generator: yields the first folder file, then expires mid-item
        dest_dir.mkdir(parents=True, exist_ok=True)
        if module == "resource":
            p = dest_dir / "notes.txt"
            p.write_bytes(b"DATA")
            yield p
            return
        p1 = dest_dir / "slide1.txt"
        p1.write_bytes(b"SLIDES")
        yield p1
        raise moodle.SessionExpired("login required")

    monkeypatch.setattr(materials.course, "download_module", failing_download_module)
    with pytest.raises(moodle.SessionExpired):
        materials.download_course(make_session(), 42, tmp_path)
    on_disk = materials.list_materials(tmp_path)
    assert [f["cmid"] for f in on_disk["files"]] == [1]
    assert not any(f["cmid"] == 5 for f in on_disk["files"])

    def ok_download_module(session, cmid, module, dest_dir):
        dest_dir.mkdir(parents=True, exist_ok=True)
        if module == "resource":
            p = dest_dir / "notes.txt"
            p.write_bytes(b"DATA")
            return [p]
        p1 = dest_dir / "slide1.txt"
        p1.write_bytes(b"SLIDES")
        p2 = dest_dir / "slide2.txt"
        p2.write_bytes(b"MORE")
        return [p1, p2]

    monkeypatch.setattr(materials.course, "download_module", ok_download_module)
    result = materials.download_course(make_session(), 42, tmp_path)
    assert result["skipped"] == 1
    assert result["downloaded"] == 2
    assert (tmp_path / "00 S" / "slides" / "slide2.txt").read_bytes() == b"MORE"
    assert [f["cmid"] for f in result["files"]] == [1, 5, 5]


def test_list_materials_missing_manifest(tmp_path):
    assert materials.list_materials(tmp_path) == {"files": [], "errors": []}


def test_list_materials_returns_manifest(tmp_path, monkeypatch):
    patch_course(monkeypatch)
    materials.download_course(make_session(), 42, tmp_path)
    assert materials.list_materials(tmp_path)["course_id"] == 42


def test_safe_name_rules():
    assert materials.safe('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert materials.safe("  ..dots..  ") == "dots"
    assert materials.safe("") == "untitled"
    assert materials.safe("x" * 100) == "x" * 80
