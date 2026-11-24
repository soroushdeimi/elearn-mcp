import types
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from elearn_mcp import submit

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://elearn.ut.ac.ir"
EDIT_URL = f"{BASE}/mod/assign/view.php?id=1652923&action=editsubmission"
UPLOAD_URL = f"{BASE}/repository/repository_ajax.php?action=upload"
VIEW_URL = f"{BASE}/mod/assign/view.php"
REMOVE_URL = f"{BASE}/mod/assign/view.php?id=1652923&action=removesubmissionconfirm"


def make_session():
    return types.SimpleNamespace(client=httpx.Client(), sesskey="TESTSESSKEY", user_id=12345)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_parse_edit_form_exact_values():
    info = submit.parse_edit_form(fixture("assign_editsubmission.html"))
    assert info["itemid"] == "63532544"
    assert info["client_id"] == "6b0532e053187"
    assert info["ctx_id"] == "2966617"
    assert info["repo_id"] == "5"
    assert info["maxbytes"] == 104857600
    assert info["maxfiles"] == 20
    assert info["fields"]["files_filemanager"] == "63532544"
    assert info["fields"]["action"] == "savesubmission"
    assert info["fields"]["sesskey"] == "TESTSESSKEY"
    assert info["fields"]["id"] == "1652923"
    assert info["fields"]["userid"] == "12345"
    assert info["fields"]["lastmodified"] == "1794582383"
    assert info["fields"]["_qf__mod_assign_submission_form"] == "1"


def test_parse_edit_form_missing_form():
    with pytest.raises(submit.SubmitError, match="submission form not found"):
        submit.parse_edit_form("<html><body>no form</body></html>")


def test_parse_edit_form_missing_upload_repo():
    html = fixture("assign_editsubmission.html").replace('"type":"upload"', '"type":"other"')
    with pytest.raises(submit.SubmitError, match="upload repository not found"):
        submit.parse_edit_form(html)


def mock_edit_page(respx_mock, html=None):
    respx_mock.get(EDIT_URL).mock(
        return_value=httpx.Response(200, text=html or fixture("assign_editsubmission.html"))
    )


def test_upload_files_sends_multipart_and_final_form(tmp_path):
    session = make_session()
    f = tmp_path / "answer.pdf"
    f.write_bytes(b"PDF")
    with respx.mock as mock:
        mock_edit_page(mock)
        upload = mock.post(UPLOAD_URL).mock(
            return_value=httpx.Response(200, json={"file": {"filename": "answer.pdf"}})
        )
        final = mock.post(VIEW_URL).mock(return_value=httpx.Response(200, text="<html>ok</html>"))
        result = submit.upload_files(session, 1652923, [f])
    assert result["uploaded"] == ["answer.pdf"]
    assert result["final_url"] == VIEW_URL
    body = upload.calls[0].request.content
    assert b'name="itemid"\r\n\r\n63532544' in body
    assert b'name="repo_id"\r\n\r\n5' in body
    assert b'name="sesskey"\r\n\r\nTESTSESSKEY' in body
    assert b'name="ctx_id"\r\n\r\n2966617' in body
    assert b'name="client_id"\r\n\r\n6b0532e053187' in body
    assert b"answer.pdf" in body
    final_data = parse_qs(final.calls[0].request.content.decode())
    assert final_data["files_filemanager"] == ["63532544"]
    assert final_data["action"] == ["savesubmission"]
    assert final_data["submitbutton"] == ["ذخیره تغییرات"]


def test_upload_files_error_json_raises(tmp_path):
    session = make_session()
    f = tmp_path / "answer.pdf"
    f.write_bytes(b"PDF")
    with respx.mock as mock:
        mock_edit_page(mock)
        mock.post(UPLOAD_URL).mock(return_value=httpx.Response(200, json={"error": "file too big"}))
        with pytest.raises(submit.SubmitError, match="file too big"):
            submit.upload_files(session, 1652923, [f])


def test_upload_files_too_big_raises_before_upload(tmp_path):
    session = make_session()
    f = tmp_path / "answer.pdf"
    f.write_bytes(b"0123456789" * 2)
    html = fixture("assign_editsubmission.html").replace("104857600", "10")
    with respx.mock as mock:
        mock_edit_page(mock, html)
        upload = mock.post(UPLOAD_URL)
        with pytest.raises(submit.SubmitError, match="file too big: answer.pdf"):
            submit.upload_files(session, 1652923, [f])
        assert upload.call_count == 0


def test_upload_files_missing_file_raises(tmp_path):
    session = make_session()
    with respx.mock as mock:
        mock_edit_page(mock)
        with pytest.raises(submit.SubmitError, match="file not found: nope.pdf"):
            submit.upload_files(session, 1652923, [tmp_path / "nope.pdf"])


def test_upload_files_too_many_raises(tmp_path):
    session = make_session()
    f1, f2 = tmp_path / "a.pdf", tmp_path / "b.pdf"
    f1.write_bytes(b"a")
    f2.write_bytes(b"b")
    html = fixture("assign_editsubmission.html").replace('"maxfiles":"20"', '"maxfiles":"1"')
    with respx.mock as mock:
        mock_edit_page(mock, html)
        upload = mock.post(UPLOAD_URL)
        with pytest.raises(submit.SubmitError, match="too many files: 2 > 1"):
            submit.upload_files(session, 1652923, [f1, f2])
        assert upload.call_count == 0


def test_remove_submission_posts_remove_form():
    session = make_session()
    with respx.mock as mock:
        mock.get(REMOVE_URL).mock(
            return_value=httpx.Response(200, text=fixture("assign_removeconfirm.html"))
        )
        post = mock.post(VIEW_URL).mock(return_value=httpx.Response(200, text="<html>ok</html>"))
        submit.remove_submission(session, 1652923)
    data = parse_qs(post.calls[0].request.content.decode())
    assert data["action"] == ["removesubmission"]
    assert data["sesskey"] == ["TESTSESSKEY"]
    assert data["userid"] == ["12345"]
    assert data["id"] == ["1652923"]


def test_upload_files_non_json_response_raises(tmp_path):
    session = make_session()
    f = tmp_path / "answer.pdf"
    f.write_bytes(b"PDF")
    with respx.mock as mock:
        mock_edit_page(mock)
        mock.post(UPLOAD_URL).mock(return_value=httpx.Response(200, text="<html>not json</html>"))
        with pytest.raises(submit.SubmitError, match="upload failed: <html>not json</html>"):
            submit.upload_files(session, 1652923, [f])


def test_upload_files_final_page_shows_form_again(tmp_path):
    session = make_session()
    f = tmp_path / "answer.pdf"
    f.write_bytes(b"PDF")
    with respx.mock as mock:
        mock_edit_page(mock)
        mock.post(UPLOAD_URL).mock(
            return_value=httpx.Response(200, json={"file": {"filename": "answer.pdf"}})
        )
        mock.post(VIEW_URL).mock(
            return_value=httpx.Response(200, text='<html><form class="mform"></form></html>')
        )
        with pytest.raises(submit.SubmitError, match="save failed, the form was shown again"):
            submit.upload_files(session, 1652923, [f])


def test_upload_files_final_url_editsubmission_raises(tmp_path):
    session = make_session()
    f = tmp_path / "answer.pdf"
    f.write_bytes(b"PDF")
    with respx.mock as mock:
        mock_edit_page(mock)
        mock.post(UPLOAD_URL).mock(
            return_value=httpx.Response(200, json={"file": {"filename": "answer.pdf"}})
        )
        mock.post(VIEW_URL).mock(return_value=httpx.Response(302, headers={"location": EDIT_URL}))
        with pytest.raises(submit.SubmitError, match="save failed, the form was shown again"):
            submit.upload_files(session, 1652923, [f])


def test_remove_submission_without_form_raises():
    session = make_session()
    with respx.mock as mock:
        mock.get(REMOVE_URL).mock(
            return_value=httpx.Response(200, text="<html><body>no form</body></html>")
        )
        with pytest.raises(submit.SubmitError, match="nothing to remove or removal not allowed"):
            submit.remove_submission(session, 1652923)
