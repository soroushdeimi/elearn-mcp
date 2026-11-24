"""Change watcher: diff elearn state between rounds and notify on changes."""

import argparse
import json
import os
import pathlib
import sys
import time

import httpx

from elearn_mcp import course, info, moodle, notify

STATE_PATH = pathlib.Path(os.environ.get("HOME", "~")) / ".local/state/elearn-mcp/state.json"


def collect(accounts, now: int, due_within_s: int = 86400) -> dict:
    snapshot = {}
    for account in accounts.ids():
        deadlines = accounts.run(account, lambda s: moodle.deadlines(s, now - 86400, 50))
        grades = accounts.run(account, info.grades)
        notifications = accounts.run(account, lambda s: info.notifications(s, 20))
        courses = {}
        for c in accounts.run(account, moodle.courses):
            try:
                sections = accounts.run(account, lambda s, cid=c["id"]: course.contents(s, cid))
            except (moodle.MoodleError, httpx.HTTPError, KeyError, ValueError) as e:
                # one broken course must not stop the round; it is treated as new next time
                print(f"skipping course {c['id']}: {e!r}", file=sys.stderr)
                continue
            courses[c["id"]] = {
                "name": c["fullname"],
                "items": {
                    str(item["cmid"]): item["name"]
                    for section in sections
                    for item in section["items"]
                },
            }
        snapshot[account] = {
            "deadlines": deadlines,
            "grades": grades,
            "notifications": notifications,
            "courses": courses,
        }
    return snapshot


def diff(old: dict | None, new: dict, now: int, due_within_s: int = 86400) -> list[dict]:
    events = []
    old = old or {}
    old_deadlines = {d["event_id"]: d for d in old.get("deadlines", [])}
    old_grades = {g["course"]: g["grade"] for g in old.get("grades", [])}
    old_notifications = {n["id"] for n in old.get("notifications", [])}
    # JSON round-trip turns int course ids into str keys
    old_courses = {int(k): v for k, v in old.get("courses", {}).items()}
    # old state files stored a plain list of cmids; treat names as unknown
    old_courses = {
        cid: {
            "name": v.get("name", str(cid)) if isinstance(v, dict) else str(cid),
            "items": v.get("items", {}) if isinstance(v, dict) else {str(c): "" for c in v},
        }
        for cid, v in old_courses.items()
    }
    sent = set(old.get("sent", []))
    # an empty snapshot (None, {}, or all-empty lists) means first run
    first_run = not old or not any(
        old.get(k) for k in ("deadlines", "grades", "notifications", "courses")
    )

    for d in new.get("deadlines", []):
        if d["event_id"] not in old_deadlines:
            events.append(
                {
                    "kind": "new_assignment",
                    "key": f"assign:{d['event_id']}",
                    "text": f"{d['course']}: {d['name']}",
                    "url": d["url"],
                }
            )
        if 0 < d["due"] - now <= due_within_s:
            key = f"due:{d['event_id']}"
            if key not in sent:
                hours = round((d["due"] - now) / 3600)
                events.append(
                    {
                        "kind": "due_soon",
                        "key": key,
                        "text": f"{d['course']}: {d['name']} ({hours} ساعت مانده)",
                        "url": d["url"],
                    }
                )

    for g in new.get("grades", []):
        if g["course"] in old_grades and old_grades[g["course"]] != g["grade"]:
            events.append(
                {
                    "kind": "new_grade",
                    "key": f"grade:{g['course']}",
                    "text": f"{g['course']}: {g['grade']}",
                    "url": g.get("course_url"),
                }
            )

    for n in new.get("notifications", []):
        if n["id"] not in old_notifications:
            events.append(
                {
                    "kind": "new_notification",
                    "key": f"notif:{n['id']}",
                    "text": f"{n['subject']}: {n['text']}",
                    "url": n.get("url"),
                }
            )

    for course_id, new_course in new.get("courses", {}).items():
        if course_id not in old_courses:
            continue  # new course in the snapshot: skip to avoid a first-run flood
        old_items = old_courses[course_id]["items"]
        for cmid, item_name in new_course["items"].items():
            if cmid not in old_items:
                events.append(
                    {
                        "kind": "new_material",
                        "key": f"material:{course_id}:{cmid}",
                        "text": f"{new_course['name']}: {item_name}",
                        "url": None,
                    }
                )

    if first_run:
        events = [e for e in events if e["kind"] == "due_soon"]
    return events


_LABELS = {
    "new_assignment": "📌 تکلیف جدید",
    "due_soon": "⏰ مهلت نزدیک",
    "new_grade": "📊 نمره جدید",
    "new_notification": "🔔 اعلان",
    "new_material": "📚 فایل جدید",
}


def message(event: dict, multi: bool = False) -> str:
    text = f"{_LABELS[event['kind']]} — {event['text']}"
    if event.get("url"):
        text += f"\n{event['url']}"
    if multi:
        text = f"[{event['account']}] {text}"
    return text


def _load_state(state_path: pathlib.Path) -> dict:
    try:
        return json.loads(state_path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(state_path: pathlib.Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    os.replace(tmp, state_path)


def run_once(
    accounts,
    state_path: pathlib.Path,
    channels: list[str],
    now: int | None = None,
    send=notify.send,
) -> list[dict]:
    now = int(time.time()) if now is None else now
    state = _load_state(pathlib.Path(state_path))
    snapshot = collect(accounts, now)
    multi = len(snapshot) > 1
    events = []
    for account, new in snapshot.items():
        old = state.get(account, {})
        for event in diff(old, new, now):
            event = dict(event)
            event["account"] = account
            send(message(event, multi), channels)
            events.append(event)
            if "key" in event:
                old.setdefault("sent", []).append(event["key"])
        state[account] = {
            "deadlines": new["deadlines"],
            "grades": new["grades"],
            "notifications": new["notifications"],
            "courses": new["courses"],
            "sent": old.get("sent", []),
        }
    _save_state(pathlib.Path(state_path), state)
    return events


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="elearn_mcp.watch")
    parser.add_argument("--channels", default="macos")
    parser.add_argument("--interval", type=int, default=900)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    from elearn_mcp.accounts import Accounts, load_credentials

    username, password = load_credentials()
    accounts = Accounts(username, password)
    while True:
        try:
            run_once(accounts, STATE_PATH, channels)
        except Exception as e:
            print(f"watch round failed: {e}", file=sys.stderr)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
