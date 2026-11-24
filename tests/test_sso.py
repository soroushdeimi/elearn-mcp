from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup

import elearn_mcp.arvan as arvan
from elearn_mcp import sso

FIXTURES = Path(__file__).parent / "fixtures"
SSO_LOGIN_URL = "https://sso1.ut.ac.ir/ApiContainer.SSO.RCL1/Account/Login?ReturnUrl=abc"
PICKER_URL = "https://elearn.ut.ac.ir/auth/utoauth2/login.php?wantsurl&sesskey=TESTSESSKEY&id=2&oauth2code=TESTCODE"
COURSES_URL = "https://elearn.ut.ac.ir/my/courses.php"
SINGLE_PICKER = """
<html><body><form>
<input type="radio" value="610000001" name="auth_utoauth2_username" />
<input type="submit" name="continue" value="ادامه">
</form></body></html>
"""


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def setup_routes(
    respx_mock,
    final_html=None,
    sso_post=None,
    challenge_first=False,
    picker_html=None,
    redirect_to=None,
):
    """Mock the full login chain; returns the routes for request inspection."""
    if final_html is None:
        final_html = read_fixture("my_courses.html")
    if picker_html is None:
        picker_html = read_fixture("account_select.html")
    if redirect_to is None:
        redirect_to = PICKER_URL if picker_html is not None else COURSES_URL
    routes = {}
    login_route = respx_mock.get("https://elearn.ut.ac.ir/login/index.php")
    if challenge_first:
        login_route.side_effect = [
            httpx.Response(200, text=read_fixture("arvan_challenge.html")),
            httpx.Response(303, headers={"location": SSO_LOGIN_URL}),
        ]
    else:
        login_route.return_value = httpx.Response(303, headers={"location": SSO_LOGIN_URL})
    routes["login"] = login_route
    routes["sso_page"] = respx_mock.get(SSO_LOGIN_URL)
    routes["sso_page"].return_value = httpx.Response(200, text=read_fixture("sso_login.html"))
    sso_post_route = respx_mock.post("https://sso1.ut.ac.ir/ApiContainer.SSO.RCL1/Account/login")
    if sso_post is not None:
        sso_post_route.return_value = sso_post
    else:
        sso_post_route.return_value = httpx.Response(302, headers={"location": redirect_to})
    routes["sso_post"] = sso_post_route
    if picker_html is not None:
        respx_mock.get(PICKER_URL).return_value = httpx.Response(200, text=picker_html)
        picker_route = respx_mock.post(PICKER_URL)
        picker_route.return_value = httpx.Response(303, headers={"location": COURSES_URL})
        routes["picker"] = picker_route
    final_route = respx_mock.get(COURSES_URL)
    final_route.return_value = httpx.Response(200, text=final_html)
    routes["final"] = final_route
    return routes


def test_login_full_flow(respx_mock, monkeypatch):
    monkeypatch.setattr(arvan, "solve", lambda html: {"__arcsjs": "x", "__arcsjsc": "y"})
    routes = setup_routes(respx_mock)
    session = sso.login("user", "secret-pass", student_id="610000002")
    assert session.sesskey == "TESTSESSKEY"
    assert session.user_id == 12345
    assert session.student_id == "610000002"
    form = httpx.QueryParams(routes["sso_post"].calls.last.request.content.decode())
    assert form["Username"] == "user"
    assert form["Password"] == "secret-pass"
    assert form["captcha"] == ""
    assert form["button"] == "login"
    assert form["RememberLogin"] == "false"
    assert form["__RequestVerificationToken"] == "TESTTOKEN"
    soup = BeautifulSoup(read_fixture("sso_login.html"), "html.parser")
    assert form["ReturnUrl"] == soup.find("input", {"name": "ReturnUrl"})["value"]
    req = routes["sso_post"].calls.last.request
    assert req.headers["referer"] == SSO_LOGIN_URL
    assert req.headers["origin"] == "https://sso1.ut.ac.ir"
    preq = routes["picker"].calls.last.request
    pform = httpx.QueryParams(preq.content.decode())
    assert pform["auth_utoauth2_username"] == "610000002"
    assert pform["continue"] == "ادامه"
    assert preq.headers["referer"] == PICKER_URL


def test_accounts_lists_picker_ids(respx_mock):
    setup_routes(respx_mock)
    assert sso.accounts("user", "pw") == ["610000001", "610000002"]


def test_login_without_student_id_raises_choice(respx_mock):
    setup_routes(respx_mock)
    with pytest.raises(sso.AccountChoiceRequired) as ei:
        sso.login("user", "pw")
    assert ei.value.accounts == ["610000001", "610000002"]


def test_login_unknown_student_id_lists_available(respx_mock):
    setup_routes(respx_mock)
    with pytest.raises(sso.LoginError, match="610000001.*610000002"):
        sso.login("user", "pw", student_id="999")


def test_login_single_picker_picks_it(respx_mock):
    setup_routes(respx_mock, picker_html=SINGLE_PICKER)
    session = sso.login("user", "pw")
    assert session.student_id == "610000001"


def test_login_no_picker_uses_page(respx_mock):
    setup_routes(respx_mock, redirect_to=COURSES_URL)
    session = sso.login("user", "pw")
    assert session.student_id is None


def test_sso_rejected_login_hides_password(respx_mock):
    rejected = (
        '<html><body><div class="validation-summary-errors">'
        "<ul><li>نام کاربری یا رمز عبور اشتباه است</li></ul></div></body></html>"
    )
    setup_routes(respx_mock, sso_post=httpx.Response(200, text=rejected))
    with pytest.raises(sso.LoginError, match="SSO rejected the login") as ei:
        sso.login("user", "topsecret-pw")
    assert "topsecret-pw" not in str(ei.value)


def test_arvan_challenge_solved_and_retried(respx_mock, monkeypatch):
    monkeypatch.setattr(arvan, "solve", lambda html: {"__arcsjs": "x", "__arcsjsc": "y"})
    routes = setup_routes(respx_mock, challenge_first=True)
    session = sso.login("user", "pw", student_id="610000002")
    assert session.sesskey == "TESTSESSKEY"
    assert routes["login"].call_count == 2
    assert session.client.cookies["__arcsjs"] == "x"
    assert session.client.cookies["__arcsjsc"] == "y"


def test_final_page_not_logged_in(respx_mock):
    final = (
        '<script>M.cfg = {"wwwroot":"https://elearn.ut.ac.ir","sesskey":"K","userId":0};</script>'
    )
    setup_routes(respx_mock, final_html=final, redirect_to=COURSES_URL)
    with pytest.raises(sso.LoginError, match="not logged in"):
        sso.login("user", "pw")


def test_fetch_retries_429_with_retry_after_seconds(respx_mock, monkeypatch):
    sleeps = []
    monkeypatch.setattr(sso, "_sleep", sleeps.append)
    route = respx_mock.get(
        "https://sso1.ut.ac.ir/ApiContainer.SSO.RCL1/Account/Login?ReturnUrl=abc"
    )
    route.side_effect = [
        httpx.Response(
            429,
            json={"error": "temporarily_unavailable", "retryAfterSeconds": 2},
        ),
        httpx.Response(200, text="<html>ok</html>"),
    ]
    client = sso.new_client()
    resp = sso.fetch(client, "GET", SSO_LOGIN_URL)
    assert resp.status_code == 200
    assert resp.text == "<html>ok</html>"
    assert sleeps == [2.5]


def test_fetch_429_exhausts_retries(respx_mock, monkeypatch):
    sleeps = []
    monkeypatch.setattr(sso, "_sleep", sleeps.append)
    respx_mock.get(SSO_LOGIN_URL).return_value = httpx.Response(429, json={"retryAfterSeconds": 1})
    client = sso.new_client()
    with pytest.raises(sso.LoginError, match="rate limit"):
        sso.fetch(client, "GET", SSO_LOGIN_URL)
    assert len(sleeps) == 3


def test_fetch_429_uses_retry_after_header(respx_mock, monkeypatch):
    sleeps = []
    monkeypatch.setattr(sso, "_sleep", sleeps.append)
    respx_mock.get(SSO_LOGIN_URL).side_effect = [
        httpx.Response(429, text="no json", headers={"Retry-After": "5"}),
        httpx.Response(200, text="<html>ok</html>"),
    ]
    client = sso.new_client()
    sso.fetch(client, "GET", SSO_LOGIN_URL)
    assert sleeps == [5.5]


def test_fetch_429_caps_delay_at_30(respx_mock, monkeypatch):
    sleeps = []
    monkeypatch.setattr(sso, "_sleep", sleeps.append)
    respx_mock.get(SSO_LOGIN_URL).side_effect = [
        httpx.Response(429, json={"retryAfterSeconds": 100}),
        httpx.Response(200, text="<html>ok</html>"),
    ]
    client = sso.new_client()
    sso.fetch(client, "GET", SSO_LOGIN_URL)
    assert sleeps == [30.5]


def test_sso_page_without_form_raises_login_error(respx_mock):
    routes = setup_routes(respx_mock)
    routes["sso_page"].return_value = httpx.Response(200, text="<html></html>")
    with pytest.raises(sso.LoginError, match="form not found"):
        sso.login("user", "pw")


def test_login_reuses_existing_sso_session(respx_mock):
    respx_mock.get("https://elearn.ut.ac.ir/login/index.php").return_value = httpx.Response(
        303, headers={"location": PICKER_URL}
    )
    sso_post_route = respx_mock.post("https://sso1.ut.ac.ir/ApiContainer.SSO.RCL1/Account/login")
    respx_mock.get(PICKER_URL).return_value = httpx.Response(
        200, text=read_fixture("account_select.html")
    )
    respx_mock.post(PICKER_URL).return_value = httpx.Response(
        303, headers={"location": COURSES_URL}
    )
    respx_mock.get(COURSES_URL).return_value = httpx.Response(
        200, text=read_fixture("my_courses.html")
    )
    session = sso.login("user", "pw", student_id="610000002")
    assert session.sesskey == "TESTSESSKEY"
    assert session.student_id == "610000002"
    assert sso_post_route.call_count == 0


def test_clone_sso_cookies_copies_only_sso_and_arcs():
    client = sso.new_client()
    client.cookies.set("sso_session", "abc", domain="sso1.ut.ac.ir", path="/")
    client.cookies.set("__arcsjs", "x", domain=".ut.ac.ir", path="/")
    client.cookies.set("MoodleSession", "m", domain="elearn.ut.ac.ir", path="/")
    clone = sso.clone_sso_cookies(client)
    assert clone.cookies["sso_session"] == "abc"
    assert clone.cookies["__arcsjs"] == "x"
    assert "MoodleSession" not in clone.cookies


def test_parse_cfg_missing_raises():
    with pytest.raises(sso.LoginError, match="no M.cfg"):
        sso.parse_cfg("<html><body>no config</body></html>")


def test_parse_cfg_returns_dict():
    cfg = sso.parse_cfg(read_fixture("my_courses.html"))
    assert cfg["sesskey"] == "TESTSESSKEY"
    assert cfg["userId"] == 12345
