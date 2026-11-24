import json
import types
from pathlib import Path

import httpx
import pytest
import respx

from elearn_mcp import course

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://elearn.ut.ac.ir"
STATE = "core_courseformat_get_state"
STATE_URL = f"{BASE}/lib/ajax/service.php?sesskey=TESTSESSKEY&info={STATE}"
FILE_URL = f"{BASE}/pluginfile.php/2875753/mod_resource/content/0/ref.pdf"


def make_session():
    return types.SimpleNamespace(client=httpx.Client(), sesskey="TESTSESSKEY", user_id=12345)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_contents_maps_fixture():
    session = make_session()
    with respx.mock:
        respx.post(STATE_URL).mock(
            return_value=httpx.Response(200, text=fixture("ajax_courseformat_state.json"))
        )
        sections = course.contents(session, 105649)
    assert [s["title"] for s in sections] == [
        "نیمسال اول سال تحصيلي 406-1405",
        "منابع",
        "محتوای آموزشی",
    ]
    assert [s["number"] for s in sections] == [0, 1, 2]
    assert all(s["visible"] for s in sections)
    assert sections[0]["items"][0] == {
        "cmid": 1492226,
        "name": "تالار اعلانات درس",
        "module": "forum",
        "url": "https://elearn.ut.ac.ir/mod/forum/view.php?id=1492226",
        "visible": True,
    }
    assert [i["cmid"] for i in sections[1]["items"]] == [
        1606449,
        1602802,
        1602795,
        1602796,
        1602798,
        1602799,
        1637542,
    ]
    assert sections[2]["items"][0]["module"] == "folder"


def test_contents_skips_missing_cm():
    session = make_session()
    payload = json.loads(fixture("ajax_courseformat_state.json"))
    state = json.loads(payload[0]["data"])
    state["cm"] = [cm for cm in state["cm"] if cm["id"] != "1492226"]
    payload[0]["data"] = json.dumps(state)
    with respx.mock:
        respx.post(STATE_URL).mock(return_value=httpx.Response(200, text=json.dumps(payload)))
        sections = course.contents(session, 105649)
    assert [i["cmid"] for i in sections[0]["items"]] == [1495031, 1495689]


def test_resource_url_returns_pluginfile_url():
    session = make_session()
    with respx.mock:
        respx.get(f"{BASE}/mod/resource/view.php?id=1602802&redirect=1").mock(
            return_value=httpx.Response(303, headers={"location": FILE_URL})
        )
        url = course.resource_url(session, 1602802)
    assert url == FILE_URL


def test_resource_url_sso_redirect_raises_session_expired():
    session = make_session()
    with respx.mock:
        respx.get(f"{BASE}/mod/resource/view.php?id=1602802&redirect=1").mock(
            return_value=httpx.Response(
                303,
                headers={"location": "https://sso1.ut.ac.ir/ApiContainer.SSO.RCL1/Account/Login"},
            )
        )
        with pytest.raises(course.SessionExpired) as ei:
            course.resource_url(session, 1602802)
    assert ei.value.errorcode == "requireloginerror"


def test_resource_url_login_redirect_raises_session_expired():
    session = make_session()
    with respx.mock:
        respx.get(f"{BASE}/mod/resource/view.php?id=1602802&redirect=1").mock(
            return_value=httpx.Response(303, headers={"location": f"{BASE}/login/index.php"})
        )
        with pytest.raises(course.SessionExpired):
            course.resource_url(session, 1602802)


def test_resource_url_non_pluginfile_redirect_raises():
    session = make_session()
    with respx.mock:
        respx.get(f"{BASE}/mod/resource/view.php?id=1602802&redirect=1").mock(
            return_value=httpx.Response(
                303, headers={"location": f"{BASE}/mod/resource/view.php?id=1602802"}
            )
        )
        with pytest.raises(course.MoodleError, match="not a downloadable resource"):
            course.resource_url(session, 1602802)


def test_resource_url_html_response_raises():
    session = make_session()
    with respx.mock:
        respx.get(f"{BASE}/mod/resource/view.php?id=1602802&redirect=1").mock(
            return_value=httpx.Response(200, text="<html>page</html>")
        )
        with pytest.raises(course.MoodleError, match="not a downloadable resource"):
            course.resource_url(session, 1602802)


def test_folder_files_lists_fixture_files():
    files = course.folder_files(fixture("folder_view.html"))
    assert len(files) == 16
    assert files[0] == {
        "name": "00_intro.pptx",
        "url": f"{BASE}/pluginfile.php/2875753/mod_folder/content/0/00_intro.pptx?forcedownload=1",
    }
    assert files[-1]["name"] == "15_ML_intro.pptx"


def test_external_url_from_fixture():
    url = course.external_url(fixture("url_view.html"))
    assert (
        url
        == "https://docs.google.com/spreadsheets/d/1leXM6skJd514HEpV7U-a1UqhPKhaegC_u8yKumyne2A/edit?usp=sharing"
    )


def test_external_url_none_when_absent():
    assert course.external_url("<html><body>no link</body></html>") is None


def test_download_module_resource(tmp_path):
    session = make_session()
    with respx.mock:
        respx.get(f"{BASE}/mod/resource/view.php?id=1602802&redirect=1").mock(
            return_value=httpx.Response(303, headers={"location": FILE_URL})
        )
        respx.get(f"{FILE_URL}?forcedownload=1").mock(
            return_value=httpx.Response(
                200, content=b"PDFBYTES", headers={"content-type": "application/pdf"}
            )
        )
        paths = course.download_module(session, 1602802, "resource", tmp_path)
    assert paths == [tmp_path / "ref.pdf"]
    assert paths[0].read_bytes() == b"PDFBYTES"


def test_download_module_folder(tmp_path):
    session = make_session()
    with respx.mock:
        respx.get(f"{BASE}/mod/folder/view.php?id=1591720").mock(
            return_value=httpx.Response(200, text=fixture("folder_view.html"))
        )
        names = [f["name"] for f in course.folder_files(fixture("folder_view.html"))]
        for name in names:
            url = f"{BASE}/pluginfile.php/2875753/mod_folder/content/0/{name}?forcedownload=1"
            respx.get(url).mock(
                return_value=httpx.Response(
                    200, content=b"SLIDES", headers={"content-type": "application/octet-stream"}
                )
            )
        paths = course.download_module(session, 1591720, "folder", tmp_path)
    assert [p.name for p in paths] == names
    assert (tmp_path / "00_intro.pptx").read_bytes() == b"SLIDES"


def test_download_module_unknown_module_raises():
    session = make_session()
    with pytest.raises(ValueError):
        course.download_module(session, 1492226, "forum", Path("/tmp"))


def test_contents_item_without_url(monkeypatch):
    payload = json.loads(fixture("ajax_courseformat_state.json"))
    state = json.loads(payload[0]["data"])
    del state["cm"][0]["url"]  # labels come without a url
    monkeypatch.setattr(course.moodle, "call", lambda s, m, a: json.dumps(state))
    items = [i for sec in course.contents(None, 1) for i in sec["items"]]
    assert any(i["cmid"] == int(state["cm"][0]["id"]) and i["url"] is None for i in items)
