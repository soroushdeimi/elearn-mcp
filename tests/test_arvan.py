import importlib.resources
import subprocess
from pathlib import Path

import pytest

import elearn_mcp.arvan as arvan
from elearn_mcp.arvan import ChallengeError, is_challenge, solve

FIXTURES = Path(__file__).parent / "fixtures"
# Golden values from the reference solver on arvan_challenge.html
GOLDEN = {
    "__arcsjs": "61e42b06e131d94c7542d555740255b3",
    "__arcsjsc": "arcookie-1795469160-b2c1eae2d6fb935711e6755b5b17b26a",
}


def test_solve_fixture_returns_golden_values():
    html = (FIXTURES / "arvan_challenge.html").read_text()
    assert solve(html) == GOLDEN


def test_is_challenge_true_for_fixture():
    html = (FIXTURES / "arvan_challenge.html").read_text()
    assert is_challenge(html) is True


def test_is_challenge_false_for_sso_login():
    html = (FIXTURES / "sso_login.html").read_text()
    assert is_challenge(html) is False


def test_solve_without_hash_script_raises():
    with pytest.raises(ChallengeError):
        solve("<html>no script</html>")


def test_solve_missing_node_binary_raises():
    html = (FIXTURES / "arvan_challenge.html").read_text()
    with pytest.raises(ChallengeError, match="node not found"):
        solve(html, node="/nonexistent/node")


def test_packaged_arvan_js_is_in_package():
    path = importlib.resources.files("elearn_mcp") / "arvan.js"
    assert path.is_file()
    assert "hash_v1" in path.read_text()


def test_solve_timeout_raises(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="node", timeout=1)

    monkeypatch.setattr(arvan.subprocess, "run", fake_run)
    with pytest.raises(ChallengeError, match="timed out"):
        solve("<html>no script</html>")


def test_page_script_cannot_write_files(tmp_path):
    escape = (
        "this.constructor.constructor("
        '"return process")().mainModule.require("fs")'
        f'.writeFileSync("{tmp_path / "pwned"}", "x");'
    )
    html = f"<html><script>var hash_v1 = 'a'; var hash = 'b'; {escape}</script></html>"
    with pytest.raises(ChallengeError):
        solve(html)
    assert not (tmp_path / "pwned").exists()


def test_solve_missing_arcsjsc_key_raises(monkeypatch):
    proc = subprocess.CompletedProcess(args=["node"], returncode=0, stdout='{"arcsjs": "a"}')
    monkeypatch.setattr(arvan.subprocess, "run", lambda *a, **k: proc)
    with pytest.raises(ChallengeError):
        solve("<html>no script</html>")
