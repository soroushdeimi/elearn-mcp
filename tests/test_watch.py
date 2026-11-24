import json

from elearn_mcp import watch

NOW = 1_700_000_000


def deadline(event_id, course="C1", name="hw", due=NOW + 3600, cmid=1):
    return {
        "event_id": event_id,
        "name": name,
        "module": "assign",
        "course_id": 10,
        "course": course,
        "cmid": cmid,
        "url": f"https://elearn.ut.ac.ir/mod/assign/view.php?id={cmid}",
        "due": due,
        "overdue": False,
    }


def grade(course="C1", grade="10.00"):
    return {
        "course": course,
        "course_url": "https://elearn.ut.ac.ir/course/view.php?id=10",
        "grade": grade,
    }


def notification(nid, subject="s", text="t"):
    return {
        "id": nid,
        "subject": subject,
        "text": text,
        "url": "https://elearn.ut.ac.ir/",
        "time": NOW,
        "read": 0,
        "type": "x",
    }


def course(name="C1", items=None):
    return {"name": name, "items": dict(items or {})}


def snap(deadlines=(), grades=(), notifications=(), courses=None, sent=None):
    return {
        "deadlines": list(deadlines),
        "grades": list(grades),
        "notifications": list(notifications),
        "courses": courses or {},
        "sent": list(sent or []),
    }


def kinds(events):
    return [e["kind"] for e in events]


def test_new_assignment_fires_for_new_event_id():
    old = snap(deadlines=[deadline(1, due=NOW + 200000)])
    new = snap(deadlines=[deadline(1, due=NOW + 200000), deadline(2, name="hw2", due=NOW + 200000)])
    events = watch.diff(old, new, NOW)
    assert kinds(events) == ["new_assignment"]
    assert events[0]["text"] == "C1: hw2"
    assert events[0]["url"].endswith("id=1")


def test_due_soon_fires_once():
    old = snap(deadlines=[deadline(1, due=NOW + 3600)])
    new = snap(deadlines=[deadline(1, due=NOW + 3600)])
    events = watch.diff(old, new, NOW)
    assert kinds(events) == ["due_soon"]
    # second diff with the key recorded in sent: no repeat
    new2 = snap(deadlines=[deadline(1, due=NOW + 3600)], sent=["due:1"])
    assert watch.diff(new2, new2, NOW) == []


def test_due_soon_window_edges():
    # due in the past: not due_soon
    assert kinds(watch.diff(snap(), snap(deadlines=[deadline(1, due=NOW - 10)]), NOW)) == []
    # due exactly at the window edge: fires
    edge = snap(deadlines=[deadline(1, due=NOW + 86400)])
    assert kinds(watch.diff(snap(), edge, NOW)) == ["due_soon"]
    # just past the window: does not fire
    past = snap(deadlines=[deadline(1, due=NOW + 86401)])
    assert kinds(watch.diff(snap(), past, NOW)) == []


def test_new_grade_fires_on_change_and_none_to_value():
    old = snap(grades=[grade(grade="10.00"), grade(course="C2", grade=None)])
    new = snap(grades=[grade(grade="10.00"), grade(course="C2", grade="15.50")])
    events = watch.diff(old, new, NOW)
    assert kinds(events) == ["new_grade"]
    assert events[0]["text"] == "C2: 15.50"
    # unchanged grade: no event
    assert watch.diff(new, new, NOW) == []


def test_new_notification_fires_for_new_id():
    old = snap(notifications=[notification(1)])
    new = snap(notifications=[notification(1), notification(2, subject="new")])
    events = watch.diff(old, new, NOW)
    assert kinds(events) == ["new_notification"]
    assert events[0]["text"] == "new: t"


def test_new_material_fires_for_new_cmid_in_existing_course():
    old = snap(courses={10: course(items={"1": "a", "2": "b"})})
    new = snap(courses={10: course(items={"1": "a", "2": "b", "3": "c"})})
    events = watch.diff(old, new, NOW)
    assert kinds(events) == ["new_material"]
    assert events[0]["key"] == "material:10:3"
    assert events[0]["text"] == "C1: c"


def test_new_material_old_format_state_no_crash_no_flood():
    old = snap(courses={10: [1, 2]})
    new = snap(courses={10: course(items={"1": "a", "2": "b"})})
    assert watch.diff(old, new, NOW) == []
    # a genuinely new item still fires, with the unknown old names ignored
    new2 = snap(courses={10: course(items={"1": "a", "2": "b", "3": "c"})})
    events = watch.diff(old, new2, NOW)
    assert kinds(events) == ["new_material"]
    assert events[0]["key"] == "material:10:3"


def test_new_course_in_snapshot_gives_no_new_material():
    old = snap(courses={10: course(items={"1": "a"})})
    new = snap(
        courses={
            10: course(items={"1": "a"}),
            20: course(name="C2", items={"5": "x", "6": "y"}),
        }
    )
    assert watch.diff(old, new, NOW) == []


def test_first_run_has_no_flood():
    new = snap(
        deadlines=[deadline(1, due=NOW + 3600), deadline(2, due=NOW + 200000)],
        grades=[grade(grade="10.00")],
        notifications=[notification(1)],
        courses={10: course(items={"1": "a", "2": "b"})},
    )
    events = watch.diff(None, new, NOW)
    assert kinds(events) == ["due_soon"]
    assert watch.diff({}, new, NOW) == events


def test_diff_is_pure():
    old = snap(deadlines=[deadline(1)])
    new = snap(deadlines=[deadline(1), deadline(2)])
    first = watch.diff(old, new, NOW)
    second = watch.diff(old, new, NOW)
    assert first == second
    assert old["deadlines"] == [deadline(1)]


class FakeAccounts:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def ids(self):
        return list(self._snapshot)

    def run(self, account, fn):
        return fn(None)


def test_run_once_saves_state_and_sends_events(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    snapshot = {
        "12345": snap(
            deadlines=[deadline(1, due=NOW + 3600), deadline(2, due=NOW + 200000)],
            grades=[grade(grade="10.00")],
            notifications=[notification(1)],
            courses={10: course(items={"1": "a"})},
        )
    }
    monkeypatch.setattr(watch, "collect", lambda accounts, now, due_within_s=86400: snapshot)
    sent = []

    def record(text, ch):
        sent.append(text)

    events = watch.run_once(FakeAccounts(snapshot), state_path, ["macos"], now=NOW, send=record)
    # first run: only due_soon
    assert kinds(events) == ["due_soon"]
    assert events[0]["account"] == "12345"
    assert events[0]["text"] == "C1: hw (1 ساعت مانده)"
    assert sent == [
        "⏰ مهلت نزدیک — C1: hw (1 ساعت مانده)\nhttps://elearn.ut.ac.ir/mod/assign/view.php?id=1"
    ]
    state = json.loads(state_path.read_text())
    assert state["12345"]["sent"] == ["due:1"]
    assert state["12345"]["deadlines"] == snapshot["12345"]["deadlines"]

    # second run, no changes: nothing sent
    sent.clear()
    events = watch.run_once(FakeAccounts(snapshot), state_path, ["macos"], now=NOW, send=record)
    assert events == []
    assert sent == []


def test_run_once_sends_each_change_on_second_run(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    first = {
        "12345": snap(
            deadlines=[deadline(1)],
            grades=[grade(grade=None)],
            courses={10: course(items={"1": "a"})},
        )
    }
    second = {
        "12345": snap(
            deadlines=[deadline(1), deadline(2, due=NOW + 3600)],
            grades=[grade(grade="10.00")],
            notifications=[notification(1)],
            courses={10: course(items={"1": "a", "2": "b"})},
        )
    }
    monkeypatch.setattr(watch, "collect", lambda accounts, now, due_within_s=86400: first)
    watch.run_once(FakeAccounts(first), state_path, ["macos"], now=NOW, send=lambda text, ch: None)
    monkeypatch.setattr(watch, "collect", lambda accounts, now, due_within_s=86400: second)
    sent = []

    def record(text, ch):
        sent.append(text)

    events = watch.run_once(FakeAccounts(second), state_path, ["macos"], now=NOW, send=record)
    expected = ["due_soon", "new_assignment", "new_grade", "new_material", "new_notification"]
    assert sorted(kinds(events)) == expected
    assert len(sent) == 5
    assert {t.split(" — ")[0] for t in sent} == {watch._LABELS[k] for k in expected}
    # single account: no "[account]" prefix
    assert not any(t.startswith("[") for t in sent)


def test_run_once_missing_state_file(tmp_path, monkeypatch):
    state_path = tmp_path / "nested" / "state.json"
    snapshot = {"12345": snap(deadlines=[deadline(1, due=NOW + 3600)])}
    monkeypatch.setattr(watch, "collect", lambda accounts, now, due_within_s=86400: snapshot)
    events = watch.run_once(
        FakeAccounts(snapshot), state_path, ["macos"], now=NOW, send=lambda text, ch: None
    )
    assert kinds(events) == ["due_soon"]
    assert state_path.exists()


def test_run_once_corrupt_state_file_is_ignored(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text("{not json")
    snapshot = {"12345": snap()}
    monkeypatch.setattr(watch, "collect", lambda accounts, now, due_within_s=86400: snapshot)
    events = watch.run_once(
        FakeAccounts(snapshot), state_path, ["macos"], now=NOW, send=lambda text, ch: None
    )
    assert events == []
    assert json.loads(state_path.read_text())["12345"]["sent"] == []


def test_collect_uses_accounts_run(monkeypatch):
    calls = []

    class A:
        def ids(self):
            return ["12345"]

        def run(self, account, fn):
            calls.append(account)
            return fn(None)

    monkeypatch.setattr(watch.moodle, "deadlines", lambda s, since, limit: [deadline(1)])
    monkeypatch.setattr(watch.info, "grades", lambda s: [grade()])
    monkeypatch.setattr(watch.info, "notifications", lambda s, limit: [notification(1)])
    courses = [{"id": 10, "fullname": "C1", "shortname": "c1", "url": "u"}]
    monkeypatch.setattr(watch.moodle, "courses", lambda s: courses)
    item = {"cmid": 1, "name": "n", "module": "resource", "url": "u", "visible": True}
    sections = [{"number": 0, "title": "t", "visible": True, "items": [item]}]
    monkeypatch.setattr(watch.course, "contents", lambda s, cid: sections)
    snapshot = watch.collect(A(), NOW)
    assert snapshot["12345"]["courses"] == {10: {"name": "C1", "items": {"1": "n"}}}
    assert snapshot["12345"]["deadlines"] == [deadline(1)]
    assert calls == ["12345"] * 5


def test_message_each_kind_with_and_without_url():
    base = {"account": "12345", "url": "https://elearn.ut.ac.ir/x"}
    assert (
        watch.message({**base, "kind": "new_assignment", "text": "C1: hw"})
        == "📌 تکلیف جدید — C1: hw\nhttps://elearn.ut.ac.ir/x"
    )
    assert (
        watch.message({**base, "kind": "due_soon", "text": "C1: hw (2 ساعت مانده)"})
        == "⏰ مهلت نزدیک — C1: hw (2 ساعت مانده)\nhttps://elearn.ut.ac.ir/x"
    )
    assert (
        watch.message({**base, "kind": "new_grade", "text": "C1: 18.00"})
        == "📊 نمره جدید — C1: 18.00\nhttps://elearn.ut.ac.ir/x"
    )
    assert (
        watch.message({**base, "kind": "new_notification", "text": "s: t"})
        == "🔔 اعلان — s: t\nhttps://elearn.ut.ac.ir/x"
    )
    assert (
        watch.message({**base, "kind": "new_material", "text": "C1: file.pdf"})
        == "📚 فایل جدید — C1: file.pdf\nhttps://elearn.ut.ac.ir/x"
    )
    # no url: single line, no trailing newline
    assert (
        watch.message({"account": "12345", "kind": "new_material", "text": "C1: f", "url": None})
        == "📚 فایل جدید — C1: f"
    )
    # two accounts: prefixed with the account
    assert (
        watch.message({**base, "kind": "new_grade", "text": "C1: 18.00"}, multi=True)
        == "[12345] 📊 نمره جدید — C1: 18.00\nhttps://elearn.ut.ac.ir/x"
    )
    assert (
        watch.message({"account": "12345", "kind": "new_grade", "text": "C1: 18.00", "url": None})
        == "📊 نمره جدید — C1: 18.00"
    )


def test_due_soon_text_has_hours():
    events = watch.diff(snap(), snap(deadlines=[deadline(1, due=NOW + 7200)]), NOW)
    assert events[0]["text"] == "C1: hw (2 ساعت مانده)"
    # half an hour rounds to 0 (banker's rounding)
    events = watch.diff(snap(), snap(deadlines=[deadline(1, due=NOW + 1800)]), NOW)
    assert events[0]["text"] == "C1: hw (0 ساعت مانده)"


def test_run_once_multi_account_prefixes_messages(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    snapshot = {
        "111": snap(deadlines=[deadline(1, due=NOW + 3600)]),
        "222": snap(deadlines=[deadline(1, due=NOW + 3600)]),
    }
    monkeypatch.setattr(watch, "collect", lambda accounts, now, due_within_s=86400: snapshot)
    sent = []
    watch.run_once(
        FakeAccounts(snapshot),
        state_path,
        ["macos"],
        now=NOW,
        send=lambda text, ch: sent.append(text),
    )
    assert sent == [
        "[111] ⏰ مهلت نزدیک — C1: hw (1 ساعت مانده)\nhttps://elearn.ut.ac.ir/mod/assign/view.php?id=1",
        "[222] ⏰ مهلت نزدیک — C1: hw (1 ساعت مانده)\nhttps://elearn.ut.ac.ir/mod/assign/view.php?id=1",
    ]


def test_collect_skips_a_broken_course(monkeypatch):
    class FakeAccounts:
        def ids(self):
            return ["1001"]

        def run(self, account, fn):
            return fn(None)

    monkeypatch.setattr(watch.moodle, "deadlines", lambda s, since, limit: [])
    monkeypatch.setattr(watch.info, "grades", lambda s: [])
    monkeypatch.setattr(watch.info, "notifications", lambda s, n: [])
    monkeypatch.setattr(
        watch.moodle, "courses", lambda s: [{"id": 1, "fullname": "A"}, {"id": 2, "fullname": "B"}]
    )

    def contents(s, cid):
        if cid == 1:
            raise watch.moodle.MoodleError("boom")
        return [{"items": [{"cmid": 5, "name": "x"}]}]

    monkeypatch.setattr(watch.course, "contents", contents)
    snap = watch.collect(FakeAccounts(), 0)
    assert list(snap["1001"]["courses"]) == [2]
