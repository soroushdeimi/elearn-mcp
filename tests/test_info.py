import json
import types
from pathlib import Path

import httpx
import respx

from elearn_mcp import info

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://elearn.ut.ac.ir"
GRADES_URL = f"{BASE}/grade/report/overview/index.php"
NOTIFICATIONS = "message_popup_get_popup_notifications"
NOTIFICATIONS_URL = f"{BASE}/lib/ajax/service.php?sesskey=TESTSESSKEY&info={NOTIFICATIONS}"


def make_session():
    return types.SimpleNamespace(client=httpx.Client(), sesskey="TESTSESSKEY", user_id=12345)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_parse_grades_fixture():
    grades = info.parse_grades(fixture("grades_overview.html"))
    assert len(grades) == 26
    assert grades[0] == {
        "course": "4051610384101 نظریه محاسبه",
        "course_url": "https://elearn.ut.ac.ir/course/user.php?mode=grade&id=109867&user=12345",
        "grade": None,
    }
    assert grades[2] == {
        "course": "4042610387201 مبانی سیستم های دینامیکی",
        "course_url": "https://elearn.ut.ac.ir/course/user.php?mode=grade&id=101671&user=12345",
        "grade": "10.00",
    }


def test_parse_grades_dash_is_none():
    grades = info.parse_grades(fixture("grades_overview.html"))
    assert all(g["grade"] is None for g in grades if g["course"].startswith("4051610384801"))
    assert [g["grade"] for g in grades if g["grade"] is not None] == [
        "10.00",
        "20.00",
        "30.00",
        "40.00",
        "50.00",
        "10.00",
    ]


def test_grades_fetches_overview_page():
    session = make_session()
    with respx.mock:
        respx.get(GRADES_URL).mock(
            return_value=httpx.Response(200, text=fixture("grades_overview.html"))
        )
        grades = info.grades(session)
    assert grades[0]["course"] == "4051610384101 نظریه محاسبه"


def test_notifications_maps_fixture():
    session = make_session()
    with respx.mock:
        respx.post(NOTIFICATIONS_URL).mock(
            return_value=httpx.Response(200, text=fixture("ajax_notifications.json"))
        )
        notes = info.notifications(session)
    assert len(notes) == 3
    assert notes[0] == {
        "id": 214157804,
        "subject": "Due on سه‌شنبه، 3 آذر 1405، 11:59 عصر: تمرین سری پنجم",
        "text": "Due on سه‌شنبه، 3 آذر 1405، 11:59 عصر: تمرین سری پنجم",
        "url": "https://elearn.ut.ac.ir/mod/assign/view.php?id=1658730&action=view",
        "time": 1795383267,
        "read": False,
        "type": "assign_due_soon",
    }


def test_notifications_sends_useridto_and_limit():
    session = make_session()
    seen = {}

    def capture(request: httpx.Request):
        seen["args"] = json.loads(request.content)[0]["args"]
        return httpx.Response(200, text=fixture("ajax_notifications.json"))

    with respx.mock:
        respx.post(NOTIFICATIONS_URL).mock(side_effect=capture)
        info.notifications(session, limit=5)
    assert seen["args"] == {"useridto": 12345, "newestfirst": 1, "limit": 5, "offset": 0}


def test_submitted_files_fixture():
    files = info.submitted_files(fixture("assign_submitted.html"))
    assert files == [
        {
            "name": "ai_ca1_610000001.zip",
            "url": "https://elearn.ut.ac.ir/pluginfile.php/2902863/assignsubmission_file/"
            "submission_files/2048215/ai_ca1_610000001.zip?forcedownload=1",
        }
    ]


def test_submitted_files_absent():
    assert info.submitted_files(fixture("assign_view.html")) == []


def test_feedback_synthetic_fixture():
    fb = info.feedback(fixture("assign_feedback.html"))
    assert fb == {"grade": "85.00 / 100.00", "comments": "Good work"}


def test_feedback_real_markup_without_comments():
    fb = info.feedback(fixture("assign_feedback_real.html"))
    assert fb == {"grade": "96.00 / 100.00", "comments": None}


def test_feedback_absent():
    assert info.feedback(fixture("assign_view.html")) == {"grade": None, "comments": None}
