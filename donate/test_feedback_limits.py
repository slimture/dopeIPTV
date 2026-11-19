#!/usr/bin/env python3
"""The feedback store cannot be filled by anyone (feedback.py, donate.py):
texts kept to what the app and the site's box send, in bytes; a day's room
after which a report keeps its start; a full file making room by its oldest
reports instead of refusing; the answers' index read only where it grew;
and the admins told one notice at a time.

Plain Python: ``python3 donate/test_feedback_limits.py`` runs them all
(pytest finds them too). Everything in a temporary directory.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import feedback  # noqa: E402

CODE = "c0de" * 16


def _fresh() -> str:
    feedback._seen.clear()
    feedback._ASKING.update(key=None, rows=[], at=0)
    return tempfile.mkdtemp()


def _lines(d: str) -> list[dict]:
    with open(os.path.join(d, "feedback.jsonl"), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def _body(text: str, **more) -> dict:
    return {"kind": "feedback", "version": "1.6.7", "os": "Linux", "topic": "idea", "text": text, **more}


def test_a_text_is_kept_to_what_its_sender_can_send_counted_in_bytes():
    d = _fresh()
    wide = "漢" * 47_990                         # three bytes a letter: 144 KB as characters allowed
    assert feedback.accept(d, _body(wide + "1"), "official")[0] == 200
    assert feedback.accept(d, _body(wide + "2", **{"from": "site"}), "unknown")[0] == 200
    app, site = _lines(d)
    assert len(app["text"].encode("utf-8")) <= feedback.MAX_BYTES
    assert len(site["text"].encode("utf-8")) <= feedback.SITE_BYTES and site["from"] == "site"
    # Cut between letters, never inside one.
    assert set(app["text"]) == {"漢"} and set(site["text"]) == {"漢"}
    # Plain text as long as the app sends is kept whole.
    plain = "word " * 9000
    assert feedback.accept(d, {**_body(plain), "kind": "error"}, "official")[0] == 200
    assert _lines(d)[-1]["text"] == plain.strip()


def test_a_follow_up_is_kept_short_in_bytes_too():
    d = _fresh()
    assert feedback.accept(d, _body("It stops after a while.", reply=CODE), "official")[0] == 200
    rid = _lines(d)[0]["id"]
    assert feedback.add_reply(d, "report", rid, "needinfo")
    status, got = feedback.accept(d, {**_body("ü" * 1990 + "!"), "reply": CODE, "thread": CODE}, "official")
    assert status == 200, got
    assert len(_lines(d)[-1]["text"].encode("utf-8")) <= feedback.THREAD_BYTES


def test_past_the_days_room_a_report_keeps_its_start():
    d = _fresh()
    old = feedback.DAY_BYTES
    feedback.DAY_BYTES = 30_000
    try:
        for i in range(6):
            assert feedback.accept(d, _body(f"{i} " + "long words here " * 500), "official")[0] == 200
        rows = _lines(d)
        whole = [r for r in rows if not r.get("cut")]
        cut = [r for r in rows if r.get("cut")]
        assert len(whole) == 3 and len(cut) == 3
        for r in cut:
            assert r["text"].endswith(" …") and len(r["text"].encode("utf-8")) <= feedback.LATE_BYTES + 4
        # Short ones still come whole: their start is all of them.
        assert feedback.accept(d, _body("Short and kind."), "official")[0] == 200
        assert _lines(d)[-1]["text"] == "Short and kind." and not _lines(d)[-1].get("cut")
        # A new start reads the day's room from the file.
        feedback._seen.clear()
        assert feedback.accept(d, _body("7 " + "long words here " * 500), "official")[0] == 200
        assert _lines(d)[-1].get("cut")
    finally:
        feedback.DAY_BYTES = old


def _write_old(d: str, n: int, size: int, start: float) -> list[str]:
    ids = []
    with open(os.path.join(d, "feedback.jsonl"), "a", encoding="utf-8") as fh:
        for i in range(n):
            rid = uuid.uuid4().hex[:12]
            ids.append(rid)
            fh.write(json.dumps({"id": rid, "t": int(start + i * 60), "kind": "feedback", "version": "1",
                                 "os": "x", "build": "official", "sig": "", "text": "x" * size}) + "\n")
    return ids


def test_a_full_file_makes_room_by_its_oldest_reports_and_takes_the_new_one():
    d = _fresh()
    old = feedback.MAX_FILE
    feedback.MAX_FILE = 40_000
    try:
        ids = _write_old(d, 40, 1000, time.time() - 30 * 86400)
        # A follow-up to the oldest goes with it; one to a newer report stays.
        with open(os.path.join(d, "feedback.jsonl"), "a", encoding="utf-8") as fh:
            for rid in (ids[0], ids[-1]):
                fh.write(json.dumps({"id": uuid.uuid4().hex[:12], "t": int(time.time() - 86400),
                                     "kind": "feedback", "thread_of": rid, "to": "a1", "text": "more"}) + "\n")
        assert os.path.getsize(os.path.join(d, "feedback.jsonl")) >= feedback.MAX_FILE
        status, got = feedback.accept(d, _body("A real report after an attack."), "official")
        assert status == 200, got
        rows = _lines(d)
        kept = [r["id"] for r in rows if not r.get("thread_of")]
        assert rows[-1]["text"] == "A real report after an attack."
        assert ids[0] not in kept and ids[-1] in kept
        # The oldest went first, the rest kept in order; three quarters of the file left.
        assert kept[:-1] == ids[ids.index(kept[0]):]
        assert [r["thread_of"] for r in rows if r.get("thread_of")] == [ids[-1]]
        assert os.path.getsize(os.path.join(d, "feedback.jsonl")) <= feedback.MAX_FILE * 3 // 4 + 1200
    finally:
        feedback.MAX_FILE = old


def test_a_file_nothing_can_go_from_still_refuses():
    d = _fresh()
    old = feedback.MAX_FILE
    feedback.MAX_FILE = 10_000
    try:
        with open(os.path.join(d, "feedback.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(("not json " * 20 + "\n") * 80)
        assert feedback.accept(d, _body("Hello."), "official") == (503, {"error": "full"})
    finally:
        feedback.MAX_FILE = old


def test_the_answers_index_reads_only_what_was_added():
    d = _fresh()
    path = os.path.join(d, "feedback.jsonl")
    assert feedback.accept(d, _body("First, with a code.", reply=CODE), "official")[0] == 200
    first = feedback._asking(d)
    assert [r["reply"] for r in first] == [CODE]
    at = feedback._ASKING["at"]
    assert at == os.path.getsize(path)
    other = "be" * 32
    assert feedback.accept(d, _body("Second, with another.", reply=other), "official")[0] == 200
    again = feedback._asking(d)
    # The same list, grown by the new line alone.
    assert again is first and [r["reply"] for r in again] == [CODE, other]
    assert feedback._ASKING["at"] == os.path.getsize(path) > at
    # A rewrite is another file: read whole again.
    feedback.delete(d, _lines(d)[0]["id"])
    assert [r["reply"] for r in feedback._asking(d)] == [other]
    # A file swapped in from elsewhere, or cut shorter, is read whole too.
    tmp = path + ".x"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "abc123abc123", "t": int(time.time()), "kind": "feedback",
                             "reply": CODE, "text": "swapped"}) + "\n")
    os.replace(tmp, path)
    assert [r["reply"] for r in feedback._asking(d)] == [CODE]


def test_the_admins_are_told_one_notice_at_a_time():
    root = tempfile.mkdtemp()
    conf = os.path.join(root, "donate.conf")
    with open(conf, "w") as fh:
        fh.write(f"STATS_DIR={root}\nLISTEN=127.0.0.1:0\n")
    os.environ["DONATE_CONFIG"] = conf
    sys.modules.pop("donate", None)
    import donate
    sent: list[tuple] = []
    donate.push = lambda *a, **k: sent.append(a) or (1, "")
    donate.FEEDBACK_PUSH_GAP = 0.4
    assert donate.feedback_notice("Feedback from the app", "dopeIPTV 1.6.7 · Linux") is True
    for _ in range(5):
        assert donate.feedback_notice("Feedback from the app", "dopeIPTV 1.6.7 · Linux") is False
    time.sleep(0.2)
    assert [s[0] for s in sent] == ["Feedback from the app"]
    deadline = time.time() + 5
    while len(sent) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert [s[0] for s in sent] == ["Feedback from the app", "5 more reports"]
    assert sent[1][2] == "/admin/#feedback"
    # The gap starts again from the summary: the next one waits as well.
    assert donate.feedback_notice("An error report from the app", "x") is False
    deadline = time.time() + 5
    while len(sent) < 3 and time.time() < deadline:
        time.sleep(0.05)
    assert sent[2][0] == "1 more report"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
