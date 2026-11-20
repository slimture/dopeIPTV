#!/usr/bin/env python3
"""Tests for a reply to an answer (feedback.py: accept with "thread",
add_reply's "open", replies_for, the thread in the admin's summary, item
and Copy), the archive and delete, prune keeping threads whole, and their
routes in donate.py - and the admin page asking for what is new by itself
(the revision).

Plain Python: ``python3 donate/test_feedback_threads.py`` runs them all
(pytest finds them too). Everything in a temporary directory; the service on
127.0.0.1 for the HTTP part.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import feedback  # noqa: E402
from test_official import _admin_get, _call, _service  # noqa: E402

CODE = "c0de" * 16                     # a code's SHA-256, as the app sends it


def _feedback(text: str, reply: str = CODE, **more) -> dict:
    body = {"kind": "feedback", "version": "1.6.3", "os": "Linux", "topic": "bug", "text": text, **more}
    if reply:
        body["reply"] = reply
    return body


def _follow(text: str, thread: str = CODE) -> dict:
    # The app sends the code's hash as both "reply" and "thread".
    return {"kind": "feedback", "version": "1.6.4", "os": "Linux", "text": text, "reply": thread, "thread": thread}


def _lines(d: str) -> list[dict]:
    try:
        with open(os.path.join(d, "feedback.jsonl"), encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]
    except OSError:
        return []


def _fresh() -> str:
    feedback._seen.clear()
    return tempfile.mkdtemp()


def _report(d: str, text: str = "The guide is empty after a while.", reply: str = CODE) -> str:
    status, got = feedback.accept(d, _feedback(text, reply), "official")
    assert status == 200, got
    return got["id"]


# -- the answer's switch ------------------------------------------------------------

def test_need_more_details_lets_them_reply_by_default_and_the_switch_wins():
    d = _fresh()
    rid = _report(d)
    assert feedback.add_reply(d, "report", rid, "needinfo")
    assert feedback.add_reply(d, "report", rid, "thanks")
    assert feedback.add_reply(d, "report", rid, "thanks", let_reply=True)
    assert feedback.add_reply(d, "report", rid, "needinfo", let_reply=False)
    stored = feedback._replies(d)["report"][rid]
    assert [bool(a.get("open")) for a in stored] == [True, False, True, False]
    assert all(len(a["id"]) == 10 for a in stored)
    # A group's answer never lets anyone reply: one person's thread only.
    assert feedback.add_reply(d, "group", "0123456789abcdef", "needinfo", let_reply=True)
    assert "open" not in feedback._replies(d)["group"]["0123456789abcdef"][0]


# -- a follow-up ----------------------------------------------------------------------

def test_a_follow_up_is_taken_only_while_the_latest_answer_lets_them_reply():
    d = _fresh()
    # No such report: closed, nothing kept.
    assert feedback.accept(d, _follow("More words"), "official") == (409, {"error": "closed"})
    assert _lines(d) == []
    rid = _report(d)
    before = len(_lines(d))
    assert feedback.accept(d, _follow("Before any answer"), "official")[0] == 409      # not answered yet
    feedback.add_reply(d, "report", rid, "thanks")
    assert feedback.accept(d, _follow("After a plain thanks"), "official")[0] == 409   # answered, not open
    assert len(_lines(d)) == before
    feedback.add_reply(d, "report", rid, "needinfo", "Which channel?")
    status, got = feedback.accept(d, _follow("SVT1, after ten minutes. mail me: a@b.example"), "source")
    assert status == 200 and got["ok"]
    row = _lines(d)[-1]
    to = feedback._replies(d)["report"][rid][-1]["id"]
    assert row["thread_of"] == rid and row["to"] == to and row["id"] == got["id"]
    # The hash is the report's only: never kept on the follow-up, nor anything else of the person's.
    assert "reply" not in row and "thread" not in row and "topic" not in row
    assert "<email>" in row["text"] and "a@b.example" not in row["text"]
    assert row["kind"] == "feedback" and row["version"] == "1.6.4" and row["build"] == "source"
    # One question, one answer: closed again until the next answer that lets them.
    assert feedback.accept(d, _follow("And one more thing"), "official") == (409, {"error": "closed"})
    # The same follow-up again (the app trying again): taken once.
    assert feedback.accept(d, _follow("SVT1, after ten minutes. mail me: a@b.example"), "official") \
        == (200, {"ok": True, "duplicate": True})
    feedback.add_reply(d, "report", rid, "note", "Thanks - does it happen on SVT2 too?", let_reply=True)
    assert feedback.accept(d, _follow("No, only SVT1."), "official")[0] == 200
    assert [r.get("thread_of") for r in _lines(d)] == [None, rid, rid]
    # Kept short: the person's words only.
    feedback.add_reply(d, "report", rid, "needinfo")
    assert feedback.accept(d, _follow("x" * 9000), "official")[0] == 200
    assert len(_lines(d)[-1]["text"]) == feedback.THREAD_TEXT
    assert feedback.accept(d, dict(_follow("y"), thread="not-a-hash"), "official")[0] == 400


def test_the_app_is_told_whether_the_latest_answer_lets_them_reply():
    d = _fresh()
    rid = _report(d)
    feedback.add_reply(d, "report", rid, "thanks")
    feedback.add_reply(d, "report", rid, "needinfo", "Which box?")
    got = feedback.replies_for(d, [CODE])[CODE]
    assert [a.get("open", False) for a in got] == [False, True]
    assert got[1]["text"] == "Which box?" and set(got[1]) == {"t", "code", "text", "open"}   # no ids, no bookkeeping
    feedback.accept(d, _follow("A Vu+ Duo"), "official")
    assert [a.get("open", False) for a in feedback.replies_for(d, [CODE])[CODE]] == [False, False]
    # Only the answers: the person's own words are never sent back.
    assert "A Vu+ Duo" not in json.dumps(feedback.replies_for(d, [CODE]))
    feedback.add_reply(d, "report", rid, "fixed", version="1.6.4")
    assert [a.get("open", False) for a in feedback.replies_for(d, [CODE])[CODE]] == [False, False, False]


def test_the_admin_sees_the_conversation_in_order_and_a_reply_makes_it_unread():
    d = _fresh()
    rid = _report(d, "The guide goes blank.")
    _report(d, "Another one, unanswered.", reply="")
    feedback.add_reply(d, "report", rid, "needinfo", "Which source?")
    feedback.mark(d, rid, "seen")
    s = feedback.summary(d, 30)
    it = next(i for i in s["items"] if i["id"] == rid)
    assert it["state"] == "seen" and it["open"] and not it["waiting"] and it["thread"] == 0
    feedback.accept(d, _follow("An Xtream one."), "official")
    s = feedback.summary(d, 30)
    assert s["total"] == 2 and s["kinds"]["feedback"] == 2            # a reply is not a report of its own
    assert [i["id"] for i in s["items"]][0] == rid                      # the latest activity first
    it = s["items"][0]
    assert it["state"] == "new" and it["waiting"] and not it["open"] and it["thread"] == 1
    assert s["new"] == 2
    one = feedback.item(d, rid)
    assert [e["who"] for e in one["conversation"]] == ["answer", "reply"]
    assert one["conversation"][1]["text"] == "An Xtream one." and one["conversation"][1]["state"] == "new"
    assert one["unread"] == [one["conversation"][1]["id"]] and not one["open"]
    assert "reply" not in one                                           # the hash stays here
    # Read: the report's replies marked at once, as the page does on opening it.
    assert feedback.mark(d, one["unread"], "seen")
    assert next(i for i in feedback.summary(d, 30)["items"] if i["id"] == rid)["state"] == "seen"
    assert feedback.item(d, rid)["unread"] == []
    feedback.add_reply(d, "report", rid, "fixed", "Thanks!", version="1.6.4")
    one = feedback.item(d, rid)
    assert [e["who"] for e in one["conversation"]] == ["answer", "reply", "answer"]
    assert not feedback.summary(d, 30)["items"][0]["waiting"]
    assert not feedback.mark(d, ["zz"], "seen") and not feedback.mark(d, [], "seen")


def test_copy_gives_the_whole_conversation_and_a_reply_its_own():
    d = _fresh()
    rid = _report(d, "Recording stops at midnight.")
    feedback.add_reply(d, "report", rid, "needinfo", "Which recording?")
    feedback.accept(d, _follow("The one from the guide."), "official")
    feedback.add_reply(d, "report", rid, "fixed", version="1.6.4")
    one = feedback.item(d, rid)
    text = one["copy"]
    for want in ("dopeIPTV feedback report " + rid, "Recording stops at midnight.",
                 "Answer, ", "(replies allowed):\nMore details would help. Which recording?",
                 "Reply, ", "(dopeIPTV 1.6.4, Linux):\nThe one from the guide.", "Fixed in 1.6.4."):
        assert want in text, want
    assert text.index("Recording stops") < text.index("Which recording?") < text.index("The one from") \
        < text.index("Fixed in 1.6.4.")
    reply = one["conversation"][1]
    assert reply["copy"].startswith(f"dopeIPTV reply {reply['id']} to feedback report {rid}\n")
    assert "In reply to the answer of " in reply["copy"] and "> More details would help. Which recording?" \
        in reply["copy"] and reply["copy"].endswith("\nThe one from the guide.\n")
    # Copy all shown (one read of the file): the report with its conversation, a reply's id alone.
    got = feedback.copies(d, [rid, reply["id"]])
    assert [g["id"] for g in got] == [rid, reply["id"]] and got[0]["copy"] == text
    assert got[1]["copy"] == reply["copy"]
    # The plain text of a report without answers is as it was.
    lone = _report(d, "No answer here.", reply="")
    assert feedback.item(d, lone)["copy"].endswith("\n\nNo answer here.\n")
    exported = feedback.export(d, 30)
    assert next(i for i in exported["items"] if i["id"] == rid)["follow_ups"][0]["text"] == "The one from the guide."
    assert CODE not in json.dumps(exported)


# -- archive and delete ---------------------------------------------------------------

def test_the_archive_hides_and_unhides_and_a_reply_brings_a_report_back():
    d = _fresh()
    rid = _report(d)
    other = _report(d, "Something else.", reply="")
    assert feedback.summary(d, 30)["new"] == 2
    assert feedback.archive(d, rid, True)
    s = feedback.summary(d, 30)
    it = next(i for i in s["items"] if i["id"] == rid)
    assert it["archived"] and s["new"] == 1 and s["archived"] == 1 and s["topics"] == {"bug": 1}
    assert feedback.item(d, rid)["archived"]
    assert feedback.archive(d, rid, False)
    assert not feedback.item(d, rid)["archived"] and feedback.summary(d, 30)["new"] == 2
    assert not feedback.archive(d, "../x", True)
    # Read, archived, answered with "Let them reply" - and their reply takes it out, unread.
    feedback.mark(d, rid, "seen")
    feedback.add_reply(d, "report", rid, "needinfo")
    feedback.archive(d, rid, True)
    assert next(i for i in feedback.summary(d, 30)["items"] if i["id"] == rid)["archived"]
    feedback.accept(d, _follow("Here is more."), "official")
    it = next(i for i in feedback.summary(d, 30)["items"] if i["id"] == rid)
    assert not it["archived"] and it["state"] == "new"
    # A problem archived: out of the open count, its reports out of the unread count.
    status, err = feedback.accept(d, {"kind": "error", "version": "1.6.3", "os": "Linux",
                                      "text": "Traceback:\nValueError: bad 3"}, "official")
    s = feedback.summary(d, 30)
    [g] = s["groups"]
    assert s["open_groups"] == 1 and not g["archived"]
    unread = s["new"]
    feedback.archive(d, g["key"], True)
    s = feedback.summary(d, 30)
    assert s["groups"][0]["archived"] and s["open_groups"] == 0 and s["new"] == unread - 1
    assert next(i for i in s["items"] if i["id"] == err["id"])["archived"]
    assert other


def test_delete_takes_a_report_with_its_whole_thread_and_the_app_finds_nothing():
    d = _fresh()
    rid = _report(d)
    keep = _report(d, "Keep me.", reply="d00d" * 16)
    feedback.add_reply(d, "report", rid, "needinfo")
    feedback.add_reply(d, "report", keep, "thanks")
    feedback.accept(d, _follow("More words."), "official")
    follow = _lines(d)[-1]["id"]
    feedback.mark(d, [rid, follow], "seen")
    feedback.archive(d, rid, True)
    assert feedback.replies_for(d, [CODE])            # asked before: the cache holds it
    assert feedback.delete(d, rid) == 1
    assert [r["id"] for r in _lines(d)] == [keep]
    assert feedback.replies_for(d, [CODE]) == {}      # at once, whatever the file's times say
    assert rid not in feedback._replies(d)["report"] and keep in feedback._replies(d)["report"]
    state = feedback._state(d)
    assert rid not in state and follow not in state and rid not in feedback._archived(state)
    assert feedback.item(d, rid) is None and CODE not in open(os.path.join(d, "feedback.jsonl")).read()
    # Its code opens nothing any more.
    assert feedback.accept(d, _follow("Hello?"), "official")[0] == 409
    assert feedback.delete(d, rid) == 0 and feedback.delete(d, "nope!") == -1
    assert not os.path.exists(os.path.join(d, "feedback.jsonl.tmp"))


def test_a_problem_deleted_takes_all_its_reports_and_its_answers():
    d = _fresh()
    ids = []
    for n in range(3):
        status, got = feedback.accept(d, {"kind": "error", "version": "1.6.3", "os": "Linux",
                                          "text": f"Traceback:\nValueError: bad {n}"}, "official")
        ids.append(got["id"])
    status, other = feedback.accept(d, {"kind": "error", "version": "1.6.3", "os": "Linux",
                                        "text": "Traceback:\nKeyError: x"}, "official")
    fb = _report(d, "Words.", reply="")
    key = feedback._sig_key(feedback.signature("error", "Traceback:\nValueError: bad 1"))
    feedback.add_reply(d, "group", key, "fixed", version="1.6.4")
    s = feedback.summary(d, 30)
    g = next(g for g in s["groups"] if g["key"] == key)
    assert g["count"] == 3 and g["all"] == 3 and g["state"] == "fixed"
    assert feedback.delete(d, key, group=True) == 3
    assert {r["id"] for r in _lines(d)} == {other["id"], fb}
    assert key not in feedback._replies(d)["group"] and key not in feedback._state(d)
    assert [g["key"] for g in feedback.summary(d, 30)["groups"]] != [key]


def test_several_reports_and_problems_go_in_one_pass_or_none_does():
    d = _fresh()
    a = _report(d, "First.")
    b = _report(d, "Second.", reply="d00d" * 16)
    keep = _report(d, "Keep me.", reply="beef" * 16)
    errs = []
    for n in range(2):
        status, got = feedback.accept(d, {"kind": "error", "version": "1.6.3", "os": "Linux",
                                          "text": f"Traceback:\nValueError: bad {n}"}, "official")
        errs.append(got["id"])
    key = feedback._sig_key(feedback.signature("error", "Traceback:\nValueError: bad 0"))
    feedback.add_reply(d, "report", a, "needinfo")
    before = _lines(d)
    # A bad key anywhere: nothing goes.
    assert feedback.delete_many(d, [a, "../x"], []) == -1
    assert feedback.delete_many(d, [], [key, 7]) == -1
    assert feedback.delete_many(d, [], []) == -1
    assert _lines(d) == before
    assert feedback.delete_many(d, [a, b], [key]) == 4
    assert [r["id"] for r in _lines(d)] == [keep]
    assert a not in feedback._replies(d)["report"]
    assert feedback.replies_for(d, [CODE]) == {}


def test_several_are_archived_and_marked_at_once_their_replies_too():
    d = _fresh()
    a = _report(d, "First.")
    b = _report(d, "Second.", reply="d00d" * 16)
    feedback.add_reply(d, "report", a, "needinfo", let_reply=True)
    assert feedback.accept(d, _follow("More words."), "official")[0] == 200
    follow = _lines(d)[-1]["id"]
    assert feedback.archive(d, [a, b], True)
    assert feedback.item(d, a)["archived"] and feedback.item(d, b)["archived"]
    assert feedback.archive(d, [a, b], False)
    assert not feedback.item(d, a)["archived"] and not feedback.item(d, b)["archived"]
    assert not feedback.archive(d, [a, "nope!"], True) and not feedback.item(d, a)["archived"]
    # Marked seen with their threads: the reply under a is read too.
    assert feedback.mark(d, [a, b], "seen", threads=True)
    state = feedback._state(d)
    assert state[a] == state[b] == state[follow] == "seen"
    assert not feedback.mark(d, [a, "../x"], "seen")


def test_prune_takes_a_thread_with_its_start_and_keeps_the_rest_whole():
    d = _fresh()
    now = time.time()
    old = now - (feedback.KEEP_DAYS + 3) * 86400
    rows = [{"id": "a0a0a0a0a0a0", "t": int(old), "kind": "feedback", "version": "1", "os": "L", "sig": "",
             "text": "Old", "reply": CODE},
            {"id": "b0b0b0b0b0b0", "t": int(now - 3600), "kind": "feedback", "version": "1", "os": "L",
             "sig": "", "text": "A late reply", "thread_of": "a0a0a0a0a0a0", "to": "aaaaaaaaaa"},
            {"id": "c0c0c0c0c0c0", "t": int(now - 86400), "kind": "feedback", "version": "1", "os": "L",
             "sig": "", "text": "Recent", "reply": "d00d" * 16},
            {"id": "d0d0d0d0d0d0", "t": int(now - 600), "kind": "feedback", "version": "1", "os": "L",
             "sig": "", "text": "Its reply", "thread_of": "c0c0c0c0c0c0", "to": "bbbbbbbbbb"},
            {"id": "e0e0e0e0e0e0", "t": int(now - 600), "kind": "feedback", "version": "1", "os": "L",
             "sig": "", "text": "Lost", "thread_of": "f0f0f0f0f0f0", "to": "cccccccccc"}]
    with open(os.path.join(d, "feedback.jsonl"), "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)
        fh.write("not json\n")
    with open(os.path.join(d, "feedback-replies.json"), "w", encoding="utf-8") as fh:
        json.dump({"report": {"a0a0a0a0a0a0": [{"id": "aaaaaaaaaa", "t": int(old), "code": "needinfo", "open": True}],
                              "c0c0c0c0c0c0": [{"id": "bbbbbbbbbb", "t": int(now), "code": "needinfo",
                                                "open": True}]},
                   "group": {}}, fh)
    feedback.mark(d, ["a0a0a0a0a0a0", "b0b0b0b0b0b0", "d0d0d0d0d0d0"], "seen")
    assert feedback.prune(d, now) == 1
    lines = open(os.path.join(d, "feedback.jsonl"), encoding="utf-8").read().splitlines()
    assert [json.loads(x)["id"] for x in lines[:-1]] == ["c0c0c0c0c0c0", "d0d0d0d0d0d0"] and lines[-1] == "not json"
    assert list(feedback._replies(d)["report"]) == ["c0c0c0c0c0c0"]
    state = feedback._state(d)
    assert "a0a0a0a0a0a0" not in state and "b0b0b0b0b0b0" not in state and state["d0d0d0d0d0d0"] == "seen"
    assert feedback.replies_for(d, [CODE]) == {}
    assert feedback.prune(d, now) == 0


# -- the routes -----------------------------------------------------------------------

def _admin_post(srv, donate, path, body, origin=True, signed_in=True):
    headers = {}
    if signed_in:
        headers["X-Admin-User"] = "tester"
    if origin:
        headers["Origin"] = donate.ORIGIN
    return _call(srv, "POST", path, body=body, headers=headers)


def test_the_routes_take_a_reply_say_closed_and_ask_for_what_is_new_cheaply():
    srv, stats, donate = _service()
    try:
        app = {"X-Dopeiptv-Report": "1"}
        status, got = _call(srv, "POST", "/feedback/report", "official", _feedback("Over http."), headers=app)
        rid = got["id"]
        assert _call(srv, "POST", "/feedback/report", "official", _follow("Too soon"), headers=app) \
            == (409, {"error": "closed"})
        status, first = _admin_get(srv, "/admin/feedback?days=30")
        assert status == 200 and first["rev"] and first["new"] == 1
        assert _admin_get(srv, "/admin/feedback?days=30&rev=" + first["rev"]) \
            == (200, {"rev": first["rev"], "same": True})
        assert _call(srv, "GET", "/admin/feedback?days=30&rev=" + first["rev"])[0] == 401   # signed out
        # An answer that lets them reply, from the admin page.
        assert _admin_post(srv, donate, "/admin/feedback-reply",
                           {"target": "report", "key": rid, "code": "thanks", "open": True}) == (200, {"ok": True})
        status, told = _call(srv, "POST", "/feedback/replies", None, {"codes": [CODE]}, headers=app)
        assert told["replies"][CODE][-1]["open"] is True
        assert _call(srv, "POST", "/feedback/report", "official", _follow("Now then"), headers=app)[0] == 200
        assert _call(srv, "POST", "/feedback/report", "official", _follow("And again"), headers=app)[0] == 409
        status, told = _call(srv, "POST", "/feedback/replies", None, {"codes": [CODE]}, headers=app)
        assert "open" not in told["replies"][CODE][-1]
        status, later = _admin_get(srv, "/admin/feedback?days=30&rev=" + first["rev"])
        assert status == 200 and not later.get("same") and later["rev"] != first["rev"]
        assert later["items"][0]["waiting"] and later["new"] == 1
        status, one = _admin_get(srv, "/admin/feedback-item?id=" + rid)
        assert [e["who"] for e in one["conversation"]] == ["answer", "reply"]
        assert _admin_post(srv, donate, "/admin/feedback-mark", {"keys": one["unread"], "state": "seen"})[0] == 200
        assert _admin_get(srv, "/admin/feedback-item?id=" + rid)[1]["unread"] == []
    finally:
        srv.shutdown()
        srv.server_close()


def test_archive_and_delete_routes_are_the_signed_in_admins_from_the_page_only():
    srv, stats, donate = _service()
    try:
        app = {"X-Dopeiptv-Report": "1"}
        rid = _call(srv, "POST", "/feedback/report", "official", _feedback("Archive me."), headers=app)[1]["id"]
        for path, body in (("/admin/feedback-archive", {"key": rid, "archived": True}),
                           ("/admin/feedback-delete", {"key": rid})):
            assert _admin_post(srv, donate, path, body, signed_in=False)[0] == 401
            assert _admin_post(srv, donate, path, body, origin=False)[0] == 403
        assert feedback.item(stats, rid) is not None and not feedback.item(stats, rid)["archived"]
        assert _admin_post(srv, donate, "/admin/feedback-archive", {"key": rid, "archived": True}) \
            == (200, {"ok": True})
        assert feedback.item(stats, rid)["archived"]
        assert _admin_post(srv, donate, "/admin/feedback-archive", {"key": rid, "archived": False})[0] == 200
        assert not feedback.item(stats, rid)["archived"]
        assert _admin_post(srv, donate, "/admin/feedback-archive", {"key": "../x"})[0] == 400
        assert _admin_post(srv, donate, "/admin/feedback-delete", {"key": rid}) == (200, {"ok": True, "gone": 1})
        assert feedback.item(stats, rid) is None
        status, told = _call(srv, "POST", "/feedback/replies", None, {"codes": [CODE]}, headers=app)
        assert told == {"replies": {}}
        assert _admin_post(srv, donate, "/admin/feedback-delete", {"key": "x"})[0] == 400
        assert "/admin/feedback-archive" in donate.ADMIN_POST and "/admin/feedback-delete" in donate.ADMIN_POST
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_routes_take_several_at_once_from_the_signed_in_admins_page_only():
    srv, stats, donate = _service()
    try:
        app = {"X-Dopeiptv-Report": "1"}
        ids = [_call(srv, "POST", "/feedback/report", "official",
                     _feedback(f"Report {n}.", reply=f"{n}0de" * 16), headers=app)[1]["id"] for n in range(3)]
        for path, body in (("/admin/feedback-archive", {"keys": ids[:2], "archived": True}),
                           ("/admin/feedback-mark", {"keys": ids[:2], "state": "seen", "threads": True}),
                           ("/admin/feedback-delete", {"keys": ids[:2]})):
            assert _admin_post(srv, donate, path, body, signed_in=False)[0] == 401
            assert _admin_post(srv, donate, path, body, origin=False)[0] == 403
        assert _admin_post(srv, donate, "/admin/feedback-archive", {"keys": ids[:2], "archived": True})[0] == 200
        assert all(feedback.item(stats, i)["archived"] for i in ids[:2])
        assert not feedback.item(stats, ids[2])["archived"]
        assert _admin_post(srv, donate, "/admin/feedback-mark",
                           {"keys": ids[:2], "state": "seen", "threads": True})[0] == 200
        assert all(feedback.item(stats, i)["state"] == "seen" for i in ids[:2])
        assert _admin_post(srv, donate, "/admin/feedback-delete", {"keys": [ids[0], "../x"]})[0] == 400
        assert _admin_post(srv, donate, "/admin/feedback-delete", {"keys": ids[:2]}) == (200, {"ok": True, "gone": 2})
        assert [feedback.item(stats, i) is None for i in ids] == [True, True, False]
        assert _admin_post(srv, donate, "/admin/feedback-delete", {"groups": "nope"})[0] == 400
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_page_picks_several_and_orders_what_people_wrote():
    with open(os.path.join(HERE, "admin", "feedback.js"), encoding="utf-8") as fh:
        js = fh.read()
    assert "fb-pick-bar" in js and "e.shiftKey" in js and "prune(\"g\", groups)" in js
    assert "{ groups: ids }" in js and "{ keys: ids }" in js and "window.confirm(deleteManyWords(" in js
    assert 'threads: p === "i"' in js and "box.indeterminate" in js
    assert '["oldest", "Oldest first"]' in js and '["unread", "Unread first"]' in js
    assert 'ae.type !== "checkbox"' in js and "--fb-top" in js
    with open(os.path.join(HERE, "admin", "admin.css"), encoding="utf-8") as fh:
        css = fh.read()
    for rule in (".fb-pick {", ".fb-pick-bar.on {", ".fb-row.picked .fb-line {", ".fb-line + .fb-meta {"):
        assert rule in css, rule


def test_the_page_asks_by_itself_and_has_the_reply_switch_archive_and_delete():
    with open(os.path.join(HERE, "admin", "feedback.js"), encoding="utf-8") as fh:
        js = fh.read()
    # Asked while in front only, with the revision it has; the sign-in as everywhere.
    assert 'document.visibilityState === "visible"' in js and "visibilitychange" in js
    assert '"&rev=" + encodeURIComponent' in js and "r.same" in js and "r.status === 401" in js
    assert "EVERY = 45000" in js and "count(r.new)" in js
    # Drawn in place: the open report kept with what is typed, the scroll kept, typing waited for.
    assert "bodyFor(rk, list)" in js and "keepPlace(anchor)" in js and "busy()" in js and "body.fbForm" in js
    # The conversation, the switch (on for "needinfo" until flipped), archive and delete.
    assert '"Let them reply"' in js and 'code === "needinfo"' in js and "body.open = tick.checked" in js
    assert "/admin/feedback-archive" in js and "/admin/feedback-delete" in js and "window.confirm(deleteWords(" in js
    assert '["archived", "Archived"]' in js and "They replied" in js and 'copyButton("Copy", "fb-copy", () => e.copy)' in js
    with open(os.path.join(HERE, "admin", "admin.css"), encoding="utf-8") as fh:
        css = fh.read()
    for rule in (".fb-thread {", ".fb-entry.reply {", ".fb-open {", ".fb-row.fresh {", ".chip.archived {"):
        assert rule in css, rule


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")


def test_a_quiet_thread_loses_its_code_s_hash_and_a_live_one_keeps_it():
    """After CODE_DAYS without a word - no follow-up, no answer - nothing
    asks after a report any more: the hash that ties it to the copy of the
    app that sent it goes. One with a recent answer keeps it."""
    d = _fresh()
    quiet = _report(d, "Old and done.", reply="a" * 64)
    live = _report(d, "Still going.", reply="b" * 64)
    now = time.time() + (feedback.CODE_DAYS + 1) * 86400
    data = feedback._replies(d)
    data["report"][live] = [{"id": "cccccccccc", "t": int(now) - 86400, "code": "needinfo", "open": True}]
    feedback._write_json(feedback._replies_path(d), data)
    assert feedback.forget_codes(d, now) == 1
    rows = {r["id"]: r for r in _lines(d)}
    assert "reply" not in rows[quiet] and rows[quiet]["text"]
    assert rows[live]["reply"] == "b" * 64
    assert feedback.forget_codes(d, now) == 0, "once"
