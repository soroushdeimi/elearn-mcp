import json
import subprocess
import types

import pytest
from kombu.exceptions import OperationalError

from elearn_mcp import jobs


@pytest.fixture
def eager():
    old = (jobs.app.conf.task_always_eager, jobs.app.conf.task_eager_propagates)
    jobs.app.conf.task_always_eager = True
    jobs.app.conf.task_eager_propagates = True
    yield
    jobs.app.conf.task_always_eager, jobs.app.conf.task_eager_propagates = old


@pytest.fixture
def jobs_root(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setattr(jobs, "JOBS_ROOT", root)
    return root


def agent_output(result="done", is_error=False, duration_ms=1234, cost=0.5):
    return json.dumps(
        {"result": result, "is_error": is_error, "duration_ms": duration_ms, "total_cost_usd": cost}
    )


def fake_run(stdout, stderr="", returncode=0):
    def run(command, **kwargs):
        fake_run.last = (command, kwargs)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    return run


def test_agent_job_workdir_outside_root(jobs_root):
    with pytest.raises(ValueError, match="workdir must be inside"):
        jobs.agent_job("p", str(jobs_root.parent / "elsewhere"))


def test_agent_job_dotdot_trick(jobs_root):
    with pytest.raises(ValueError, match="workdir must be inside"):
        jobs.agent_job("p", str(jobs_root / ".." / "escape"))


def test_agent_job_good_run(jobs_root, monkeypatch):
    monkeypatch.setattr(jobs.subprocess, "run", fake_run(agent_output(), stderr="warn"))
    out = jobs.agent_job("p", str(jobs_root / "w1"))
    assert out["result"] == "done"
    assert out["is_error"] is False
    assert out["duration_s"] == 1.234
    assert out["cost_usd"] == 0.5
    assert out["workdir"] == str((jobs_root / "w1").resolve())
    command, kwargs = fake_run.last
    assert command[0] == "elearn-agent"
    assert command[1:] == ["-p", "--output-format", "json", "--max-turns", "60"]
    assert kwargs["cwd"] == (jobs_root / "w1").resolve()
    assert kwargs["input"] == "p"
    assert "ELEARN_AGENT_MODEL" not in kwargs["env"]
    assert kwargs["timeout"] == 1800
    task_id = jobs.agent_job.request.id or "local"
    assert (jobs_root / "w1" / ".jobs" / f"{task_id}.json").read_text() == agent_output()
    assert (jobs_root / "w1" / ".jobs" / f"{task_id}.err").read_text() == "warn"


def test_agent_job_agent_cmd_env(jobs_root, monkeypatch):
    monkeypatch.setenv("ELEARN_AGENT_CMD", "my-agent")
    monkeypatch.setattr(jobs.subprocess, "run", fake_run(agent_output()))
    jobs.agent_job("p", str(jobs_root / "w2"), model="other-model", max_turns=5)
    command, kwargs = fake_run.last
    assert command[0] == "my-agent"
    assert command[-1] == "5"
    assert kwargs["env"]["ELEARN_AGENT_MODEL"] == "other-model"


def test_agent_job_non_json_stdout(jobs_root, monkeypatch):
    monkeypatch.setattr(jobs.subprocess, "run", fake_run("not json", stderr="boom happened"))
    out = jobs.agent_job("p", str(jobs_root / "w3"))
    assert out["is_error"] is True
    assert out["result"] == "boom happened"


def test_agent_job_non_json_stdout_truncates_stderr(jobs_root, monkeypatch):
    monkeypatch.setattr(jobs.subprocess, "run", fake_run("x", stderr="e" * 1000))
    out = jobs.agent_job("p", str(jobs_root / "w3b"))
    assert out["result"] == "e" * 500


def test_agent_job_nonzero_returncode_is_error(jobs_root, monkeypatch):
    monkeypatch.setattr(jobs.subprocess, "run", fake_run(agent_output(), returncode=1))
    out = jobs.agent_job("p", str(jobs_root / "w4"))
    assert out["is_error"] is True


def test_agent_job_timeout(jobs_root, monkeypatch):
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1800)

    monkeypatch.setattr(jobs.subprocess, "run", run)
    out = jobs.agent_job("p", str(jobs_root / "w5"))
    assert out["is_error"] is True
    assert out["result"] == "timed out after 1800 s"


def test_collect_results():
    parts = [{"is_error": False}, {"is_error": True}]
    assert jobs.collect_results(parts) == {"parts": parts, "ok": False}
    assert jobs.collect_results([{"is_error": False}])["ok"] is True


def test_fan_out_eager(jobs_root, eager, monkeypatch):
    monkeypatch.setattr(jobs.subprocess, "run", fake_run(agent_output(result="r1")))
    parts = [
        {"prompt": "p1", "workdir": str(jobs_root / "f1")},
        {"prompt": "p2", "workdir": str(jobs_root / "f2")},
    ]
    res = jobs.fan_out(parts)
    assert res.id
    out = res.result
    assert out["ok"] is True
    assert [p["workdir"] for p in out["parts"]] == sorted(
        [str((jobs_root / "f1").resolve()), str((jobs_root / "f2").resolve())]
    )


def test_fan_out_empty_parts():
    with pytest.raises(ValueError):
        jobs.fan_out([])


def fake_accounts(ids=("12345",)):
    return types.SimpleNamespace(ids=lambda: list(ids), run=lambda account, fn: fn(None))


def test_materials_job(jobs_root, monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "_accounts", lambda: fake_accounts())
    from elearn_mcp import materials, server

    monkeypatch.setattr(server, "MATERIALS_DIR", tmp_path / "materials")
    manifest = {"downloaded": 2, "skipped": 1, "errors": [], "files": [{"a": 1}, {"a": 2}]}

    def download_course(session, course_id, dest, extract=True):
        return manifest

    monkeypatch.setattr(materials, "download_course", download_course)
    out = jobs.materials_job("12345", 42)
    assert out == {
        "dir": str(tmp_path / "materials" / "12345" / "42"),
        "downloaded": 2,
        "skipped": 1,
        "errors": [],
        "files": 2,
    }


def test_materials_job_unknown_account(monkeypatch):
    monkeypatch.setattr(jobs, "_accounts", lambda: fake_accounts())
    with pytest.raises(ValueError, match="unknown student id"):
        jobs.materials_job("99999", 42)


def test_watch_round(jobs_root, monkeypatch):
    events = [{"kind": "new_assignment"}]
    seen = {}

    def run_once(accounts, state_path, channels):
        seen["accounts"] = accounts
        seen["state_path"] = state_path
        seen["channels"] = channels
        return events

    from elearn_mcp import watch

    monkeypatch.setattr(jobs, "_accounts", lambda: fake_accounts())
    monkeypatch.setattr(watch, "run_once", run_once)
    assert jobs.watch_round(["macos"]) == events
    assert seen["channels"] == ["macos"]
    assert seen["state_path"] == watch.STATE_PATH


def test_start_returns_id():
    task = types.SimpleNamespace(delay=lambda *a, **k: types.SimpleNamespace(id="abc"))
    assert jobs.start(task, 1, 2) == "abc"


def test_start_redis_down():
    def delay(*a, **k):
        raise OperationalError("connection refused")

    task = types.SimpleNamespace(delay=delay)
    with pytest.raises(jobs.JobsUnavailable, match="brew services start redis"):
        jobs.start(task)


def test_beat_schedule_env(monkeypatch):
    monkeypatch.setenv("ELEARN_WATCH_INTERVAL", "300")
    monkeypatch.setenv("ELEARN_WATCH_CHANNELS", "macos, telegram")
    schedule = jobs._beat_schedule()
    assert schedule["watch"]["task"] == "elearn.watch_round"
    assert schedule["watch"]["schedule"] == 300.0
    assert schedule["watch"]["kwargs"] == {"channels": ["macos", "telegram"]}


def test_beat_schedule_defaults():
    schedule = jobs._beat_schedule()
    assert schedule["watch"]["schedule"] == 900.0
    assert schedule["watch"]["kwargs"] == {"channels": ["macos"]}


def test_agent_job_json_without_result(jobs_root, monkeypatch):
    out_json = json.dumps({"is_error": True, "subtype": "error_max_turns"})
    monkeypatch.setattr(jobs.subprocess, "run", fake_run(out_json, returncode=1))
    out = jobs.agent_job("p", str(jobs_root / "w3"))
    assert out["is_error"] is True
    assert out["result"] == ""
