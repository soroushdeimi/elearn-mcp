import json
import types
from pathlib import Path

import httpx
import pytest
import respx

from elearn_mcp import arvan, moodle

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://elearn.ut.ac.ir"
COURSES = "core_course_get_enrolled_courses_by_timeline_classification"
EVENTS = "core_calendar_get_action_events_by_timesort"
COURSES_URL = f"{BASE}/lib/ajax/service.php?sesskey=TESTSESSKEY&info={COURSES}"
EVENTS_URL = f"{BASE}/lib/ajax/service.php?sesskey=TESTSESSKEY&info={EVENTS}"
FILE_URL = (
    f"{BASE}/pluginfile.php/2968291/mod_assign/introattachment/0/"
    "ML_Final_Project_Phase1_Graduate_Final.pdf"
)


def make_session():
    return types.SimpleNamespace(client=httpx.Client(), sesskey="TESTSESSKEY")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_call_returns_data():
    session = make_session()
    with respx.mock:
        respx.post(COURSES_URL).mock(
            return_value=httpx.Response(200, text=fixture("ajax_courses.json"))
        )
        data = moodle.call(
            session, COURSES, {"classification": "all", "limit": 0, "offset": 0, "sort": "fullname"}
        )
    assert data["courses"][0]["id"] == 82934


def test_courses_maps_first_course():
    session = make_session()
    with respx.mock:
        respx.post(COURSES_URL).mock(
            return_value=httpx.Response(200, text=fixture("ajax_courses.json"))
        )
        courses = moodle.courses(session)
    assert courses[0] == {
        "id": 82934,
        "fullname": "آنالیز ریاضی 1",
        "shortname": "4041610309501",
        "url": "https://elearn.ut.ac.ir/course/view.php?id=82934",
    }
    assert len(courses) == 3


def test_deadlines_maps_first_event():
    session = make_session()
    with respx.mock:
        respx.post(EVENTS_URL).mock(
            return_value=httpx.Response(200, text=fixture("ajax_events.json"))
        )
        events = moodle.deadlines(session, since=1795000000)
    assert events[0] == {
        "event_id": 540911,
        "name": "موعد تمرین سری پنجم سر رسیده است",
        "module": "assign",
        "course_id": 109867,
        "course": "نظریه محاسبه",
        "cmid": 1658730,
        "url": "https://elearn.ut.ac.ir/mod/assign/view.php?id=1658730",
        "due": 1795552140,
        "overdue": False,
    }


def test_invalidsesskey_raises_session_expired():
    session = make_session()
    with respx.mock:
        respx.post(COURSES_URL).mock(
            return_value=httpx.Response(200, text=fixture("ajax_error_invalidsesskey.json"))
        )
        with pytest.raises(moodle.SessionExpired) as ei:
            moodle.call(session, COURSES, {})
    assert ei.value.errorcode == "invalidsesskey"


def test_requireslogin_raises_session_expired():
    session = make_session()
    with respx.mock:
        respx.post(COURSES_URL).mock(
            return_value=httpx.Response(200, text=fixture("ajax_error_requireslogin.json"))
        )
        with pytest.raises(moodle.SessionExpired) as ei:
            moodle.call(session, COURSES, {})
    assert ei.value.errorcode == "servicerequireslogin"


def test_nopermissions_raises_plain_moodle_error():
    session = make_session()
    body = json.dumps(
        [{"error": True, "exception": {"message": "no", "errorcode": "nopermissions"}}]
    )
    with respx.mock:
        respx.post(COURSES_URL).mock(return_value=httpx.Response(200, text=body))
        with pytest.raises(moodle.MoodleError) as ei:
            moodle.call(session, COURSES, {})
    assert not isinstance(ei.value, moodle.SessionExpired)
    assert ei.value.errorcode == "nopermissions"


def test_challenge_then_json_solves_and_retries(monkeypatch):
    monkeypatch.setattr(arvan, "solve", lambda html: {"__arcsjs": "x", "__arcsjsc": "y"})
    session = make_session()
    with respx.mock:
        route = respx.post(COURSES_URL).mock(
            side_effect=[
                httpx.Response(200, text=fixture("arvan_challenge.html")),
                httpx.Response(200, text=fixture("ajax_courses.json")),
            ]
        )
        data = moodle.call(session, COURSES, {})
    assert data["courses"][0]["id"] == 82934
    assert route.call_count == 2
    assert session.client.cookies["__arcsjs"] == "x"
    assert session.client.cookies["__arcsjsc"] == "y"
    cookie = next(c for c in session.client.cookies.jar if c.name == "__arcsjs")
    assert cookie.domain == ".ut.ac.ir"
    assert cookie.path == "/"


def test_challenge_twice_raises():
    session = make_session()
    with respx.mock:
        respx.post(COURSES_URL).mock(
            return_value=httpx.Response(200, text=fixture("arvan_challenge.html"))
        )
        with pytest.raises(moodle.MoodleError, match="challenge not solved"):
            moodle.call(session, COURSES, {})


def test_non_json_body_raises():
    session = make_session()
    with respx.mock:
        respx.post(COURSES_URL).mock(return_value=httpx.Response(200, text="not json"))
        with pytest.raises(moodle.MoodleError, match="bad response"):
            moodle.call(session, COURSES, {})


def test_get_page_redirect_to_sso_raises():
    session = make_session()
    with respx.mock:
        respx.get(f"{BASE}/my/courses.php").mock(
            return_value=httpx.Response(
                302,
                headers={"location": "https://sso1.ut.ac.ir/ApiContainer.SSO.RCL1/Account/Login"},
            )
        )
        respx.get("https://sso1.ut.ac.ir/ApiContainer.SSO.RCL1/Account/Login").mock(
            return_value=httpx.Response(200, text="<html>sso</html>")
        )
        with pytest.raises(moodle.SessionExpired) as ei:
            moodle.get_page(session, f"{BASE}/my/courses.php")
    assert ei.value.errorcode == "requireloginerror"


def test_get_page_redirect_to_login_path_raises():
    session = make_session()
    with respx.mock:
        respx.get(f"{BASE}/course/view.php?id=1").mock(
            return_value=httpx.Response(302, headers={"location": f"{BASE}/login/index.php"})
        )
        respx.get(f"{BASE}/login/index.php").mock(
            return_value=httpx.Response(200, text="<html>login</html>")
        )
        with pytest.raises(moodle.SessionExpired):
            moodle.get_page(session, f"{BASE}/course/view.php?id=1")


def test_get_page_returns_html():
    session = make_session()
    with respx.mock:
        respx.get(f"{BASE}/mod/assign/view.php?id=1654147").mock(
            return_value=httpx.Response(200, text=fixture("assign_view.html"))
        )
        html = moodle.get_page(session, f"{BASE}/mod/assign/view.php?id=1654147")
    assert 'data-activityname="Phase 1"' in html


def test_download_writes_file_and_adds_forcedownload(tmp_path):
    session = make_session()
    with respx.mock:
        route = respx.get(f"{FILE_URL}?forcedownload=1").mock(
            return_value=httpx.Response(
                200, content=b"PDFBYTES", headers={"content-type": "application/pdf"}
            )
        )
        path = moodle.download(session, FILE_URL, tmp_path)
    assert route.call_count == 1
    assert path == tmp_path / "ML_Final_Project_Phase1_Graduate_Final.pdf"
    assert path.read_bytes() == b"PDFBYTES"


def test_download_rejects_non_pluginfile_url():
    session = make_session()
    with pytest.raises(ValueError):
        moodle.download(session, f"{BASE}/somefile.pdf", Path("/tmp"))


def test_download_rejects_dotdot_name(tmp_path):
    session = make_session()
    url = f"{BASE}/pluginfile.php/1/mod_assign/introattachment/0/.."
    with pytest.raises(ValueError):
        moodle.download(session, url, tmp_path)


def test_download_rejects_encoded_slash_name(tmp_path):
    session = make_session()
    url = f"{BASE}/pluginfile.php/1/mod_assign/introattachment/0/a%2Fb.pdf"
    with pytest.raises(ValueError):
        moodle.download(session, url, tmp_path)


def test_download_html_response_raises_session_expired(tmp_path):
    session = make_session()
    with respx.mock:
        respx.get(f"{FILE_URL}?forcedownload=1").mock(
            return_value=httpx.Response(
                200, content=b"<html>login</html>", headers={"content-type": "text/html"}
            )
        )
        with pytest.raises(moodle.SessionExpired):
            moodle.download(session, FILE_URL, tmp_path)
