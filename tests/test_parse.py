from pathlib import Path

from elearn_mcp.parse import parse_assignment

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_assign_view():
    html = (FIXTURES / "assign_view.html").read_text()
    result = parse_assignment(html)
    assert result["name"] == "Phase 1"
    assert result["dates"] == {
        "Opened": "Sunday, 17 Aban 1405, 12:00 AM",
        "Due": "Thursday, 5 Azar 1405, 11:59 PM",
    }
    assert result["attachments"] == [
        {
            "name": "ML_Final_Project_Phase1_Graduate_Final.pdf",
            "url": "https://elearn.ut.ac.ir/pluginfile.php/2968291/mod_assign/introattachment/0/ML_Final_Project_Phase1_Graduate_Final.pdf?forcedownload=1",
        }
    ]
    assert result["status"] == {
        "Submission status": "No submissions have been made yet",
        "Grading status": "Not graded",
        "Time remaining": "2 days 23 hours remaining",
    }
    assert result["can_submit"] is True
    assert result["cmid"] == 1654147
    assert "ML_Final_Project_Phase1_Graduate_Final.pdf" not in result["description"]
    assert result["description"] == ""


def test_parse_closed_assignment():
    html = (FIXTURES / "assign_closed.html").read_text()
    result = parse_assignment(html)
    assert result["name"] == "Closed Task"
    assert result["description"] == "This assignment is closed."
    assert result["attachments"] == []
    assert result["can_submit"] is False
    assert result["cmid"] is None
    assert result["status"] == {"Submission status": "No submissions have been made yet"}
