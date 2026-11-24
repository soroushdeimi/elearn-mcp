"""Celery app and worker tasks for the elearn pipeline."""

import json
import os
import pathlib
import subprocess
import threading

import redis.exceptions
from celery import Celery, chord, group
from kombu.exceptions import OperationalError

REDIS_URL = os.environ.get("ELEARN_REDIS_URL", "redis://127.0.0.1:6379/0")
JOBS_ROOT = pathlib.Path(
    os.environ.get("ELEARN_JOBS_ROOT", "~/Desktop/myprojects/elearn-runs")
).expanduser()

app = Celery("elearn", broker=REDIS_URL, backend=REDIS_URL)


def _beat_schedule() -> dict:
    raw = os.environ.get("ELEARN_WATCH_CHANNELS", "macos")
    channels = [c.strip() for c in raw.split(",") if c.strip()]
    return {
        "watch": {
            "task": "elearn.watch_round",
            "schedule": float(os.environ.get("ELEARN_WATCH_INTERVAL", "900")),
            "kwargs": {"channels": channels},
        }
    }


app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=7 * 24 * 3600,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    timezone="Asia/Tehran",
    broker_connection_retry_on_startup=True,
    beat_schedule=_beat_schedule(),
)


class JobsUnavailable(Exception):
    pass


_accounts_lock = threading.Lock()
_accounts_instance = None


def _accounts():
    global _accounts_instance
    if _accounts_instance is None:
        with _accounts_lock:
            if _accounts_instance is None:
                from elearn_mcp import accounts

                username, password = accounts.load_credentials()
                _accounts_instance = accounts.Accounts(username, password)
    return _accounts_instance


@app.task(name="elearn.materials")
def materials_job(account: str, course_id: int, extract: bool = True) -> dict:
    from elearn_mcp import materials, server

    if account not in _accounts().ids():
        raise ValueError(f"unknown student id: {account}")
    dest = server.MATERIALS_DIR / account / str(course_id)

    def fetch(session):
        manifest = materials.download_course(session, course_id, dest, extract=extract)
        return {
            "dir": str(dest),
            "downloaded": manifest["downloaded"],
            "skipped": manifest["skipped"],
            "errors": manifest["errors"],
            "files": len(manifest["files"]),
        }

    return _accounts().run(account, fetch)


@app.task(name="elearn.watch_round")
def watch_round(channels: list[str]) -> list[dict]:
    from elearn_mcp import watch

    return watch.run_once(_accounts(), watch.STATE_PATH, channels)


@app.task(name="elearn.agent", bind=True, soft_time_limit=1800, time_limit=1900)
def agent_job(self, prompt: str, workdir: str, model: str = "", max_turns: int = 60) -> dict:
    workdir = pathlib.Path(workdir).expanduser().resolve()
    if not workdir.is_relative_to(JOBS_ROOT.resolve()):
        raise ValueError(f"workdir must be inside {JOBS_ROOT}")
    workdir.mkdir(parents=True, exist_ok=True)
    command = [
        os.environ.get("ELEARN_AGENT_CMD", "elearn-agent"),
        "-p",
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
    ]
    env = dict(os.environ)
    if model:
        env["ELEARN_AGENT_MODEL"] = model
    task_id = self.request.id or "local"
    jobs_dir = workdir / ".jobs"
    jobs_dir.mkdir(exist_ok=True)
    try:
        proc = subprocess.run(
            command,
            cwd=workdir,
            input=prompt,
            text=True,
            capture_output=True,
            env=env,
            timeout=1800,
        )
    except subprocess.TimeoutExpired:
        (jobs_dir / f"{task_id}.err").write_text("timed out after 1800 s")
        return {
            "workdir": str(workdir),
            "result": "timed out after 1800 s",
            "is_error": True,
            "duration_s": 0,
            "cost_usd": None,
        }
    (jobs_dir / f"{task_id}.json").write_text(proc.stdout)
    (jobs_dir / f"{task_id}.err").write_text(proc.stderr)
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return {
            "workdir": str(workdir),
            "result": proc.stderr[:500],
            "is_error": True,
            "duration_s": 0,
            "cost_usd": None,
        }
    return {
        "workdir": str(workdir),
        "result": data.get("result", ""),
        "is_error": data.get("is_error", False) or proc.returncode != 0,
        "duration_s": data.get("duration_ms", 0) / 1000,
        "cost_usd": data.get("total_cost_usd"),
    }


@app.task(name="elearn.collect")
def collect_results(results: list[dict]) -> dict:
    return {"parts": results, "ok": all(not r["is_error"] for r in results)}


def fan_out(parts: list[dict], model: str = ""):
    if not parts:
        raise ValueError("no parts to fan out")
    try:
        body = group(agent_job.s(p["prompt"], p["workdir"], model) for p in parts)
        return chord(body)(collect_results.s())
    except (OperationalError, redis.exceptions.ConnectionError) as e:
        raise JobsUnavailable("redis is not running: brew services start redis") from e


def job_info(job_id: str) -> dict:
    from celery.result import AsyncResult

    try:
        result = AsyncResult(job_id, app=app)
        state = result.state
        if state == "FAILURE":
            return {"id": job_id, "state": state, "error": str(result.result)}
        if state == "SUCCESS":
            return {"id": job_id, "state": state, "result": result.result}
        return {"id": job_id, "state": state}
    except (OperationalError, redis.exceptions.ConnectionError) as e:
        raise JobsUnavailable("redis is not running: brew services start redis") from e


def start(task, *args, **kwargs) -> str:
    try:
        return task.delay(*args, **kwargs).id
    except (OperationalError, redis.exceptions.ConnectionError) as e:
        raise JobsUnavailable("redis is not running: brew services start redis") from e


def worker_main() -> None:
    args = [
        "worker",
        "--pool",
        "threads",
        "--concurrency",
        os.environ.get("ELEARN_WORKERS", "3"),
        "--loglevel",
        "INFO",
    ]
    app.worker_main(args)


def beat_main() -> None:
    schedule = pathlib.Path("~/.local/state/elearn-mcp/celerybeat-schedule").expanduser()
    schedule.parent.mkdir(parents=True, exist_ok=True)
    app.start(["beat", "--loglevel", "INFO", "--schedule", str(schedule)])
