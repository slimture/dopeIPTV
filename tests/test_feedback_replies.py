"""A reply to an answer, still anonymous: possible only under the latest
answer when the site says it lets them ("open"), once; sent as feedback in
the report's thread (its own code hash, scrubbed, within feedback's limits),
kept here with the report so its card reads as a conversation; asked about
soon again; a thread the site calls closed (409) closes here too."""
from __future__ import annotations

import time

import pytest

from dopeiptv.core import report
from tests.test_report import Inbox

_PROXIES = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")


class _Settings:
    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {})

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value) -> None:  # noqa: N802 - QSettings' name
        self.values[key] = value


# -- the tickets ------------------------------------------------------------------------

def test_a_reply_only_under_an_open_latest_answer_once_and_in_order():
    t = {"c": "a", "t": 1000, "r": [{"t": 1100, "code": "thanks"}]}
    assert not report.can_reply(t)
    t["r"].append({"t": 1200, "code": "needinfo", "open": True})
    assert report.can_reply(t)
    report.add_followup(t, "x" * 5000, 1300)
    assert t["f"] == [{"t": 1300, "w": "x" * report.REPLY_MAX, "a": 2}]
    assert not report.can_reply(t)                                   # once
    # A newer answer that lets them: once more.
    t["r"].append({"t": 1400, "code": "note", "text": "And on SVT2?", "open": True})
    assert report.can_reply(t)
    report.add_followup(t, "Not there.", 1500)
    said = [(kind, e.get("code") or e.get("w")) for kind, e in report.conversation(t)]
    assert said == [("answer", "thanks"), ("answer", "needinfo"), ("reply", "x" * report.REPLY_MAX),
                    ("answer", "note"), ("reply", "Not there.")]
    # The site said closed: closed here too, once.
    shut = {"c": "b", "t": 1, "r": [{"t": 2, "code": "needinfo", "open": True}]}
    assert report.close_thread(shut) and not report.can_reply(shut) and not report.close_thread(shut)
    # Whatever else is kept there is left out.
    odd = {"c": "c", "t": 1, "r": [{"open": True}], "f": ["junk", {"w": 3}, {"t": "x", "w": "y"}]}
    assert report.followups(odd) == [] and report.can_reply(odd)


def test_after_a_reply_answers_are_asked_soon_again_and_for_thirty_more_days():
    sent = 1_000_000.0
    t = {"c": "a", "t": sent, "r": [{"t": sent + 3600, "code": "needinfo", "open": True}]}
    later = sent + 20 * 86400
    assert report.next_ask([t], later, last=later) == report.ASK_LATER
    report.add_followup(t, "More words", later)
    # As after a new report: two minutes on, then every five minutes for a day.
    assert report.next_ask([t], later, last=later - 10) == report.ASK_FIRST
    assert report.next_ask([t], later + 120, last=later + 120) == report.ASK_SOON
    assert report.next_ask([t], later + 2 * 86400, last=later + 2 * 86400) == report.ASK_LATER
    # Answered again: as any answered report.
    t["r"].append({"t": later + 600, "code": "fixed", "version": "1.6.4"})
    assert report.next_ask([t], later + 900, last=later + 900) == report.ASK_LATER
    # Asked about for 30 days from the reply, not only from the report.
    assert report.open_codes([t], sent + 45 * 86400) == ["a"]
    assert report.open_codes([t], later + 31 * 86400) == []
    # Kept as long: the reply is activity of its own.
    now = time.time()
    old = {"c": "o", "t": now - 100 * 86400, "r": [{"t": now - 99 * 86400, "code": "needinfo", "open": True}]}
    s = _Settings()
    report.save_tickets(s, [dict(old)])
    assert report.tickets(s) == []
    report.add_followup(old, "Still here", now - 10 * 86400)
    report.save_tickets(s, [old])
    assert [x["c"] for x in report.tickets(s)] == ["o"]


def test_the_same_answers_told_without_open_replace_the_kept_ones_as_nothing_new():
    items = [{"c": "a", "t": 1, "seen": 1, "r": [{"t": 2, "code": "needinfo", "open": True}]}]
    assert report.merge_replies(items, {"a": [{"t": 2, "code": "needinfo"}]}) == 0
    assert items[0]["r"] == [{"t": 2, "code": "needinfo"}] and report.unread(items) == 0
    assert report.merge_replies(items, {}) == 0 and items[0]["r"]       # nothing said: kept


# -- sending -----------------------------------------------------------------------------

def test_a_reply_goes_with_its_thread_and_a_closed_one_is_told(monkeypatch):
    for var in _PROXIES:
        monkeypatch.delenv(var, raising=False)
    inbox, closed = Inbox(), Inbox(status=409)
    h = report.code_hash("abc")
    try:
        assert report.send_status("feedback", "More words", personal=(), endpoint=inbox.url,
                                  extra={"reply": h, "thread": h, "supporter": "yes"}) == 200
        body = inbox.got[-1][2]
        assert body["thread"] == h and body["reply"] == h and body["text"] == "More words"
        assert set(body) == {"kind", "version", "os", "text", "reply", "thread", "supporter"}
        report.send_status("feedback", "x", personal=(), endpoint=inbox.url, extra={"thread": "not-a-hash"})
        assert "thread" not in inbox.got[-1][2]
        assert report.send_status("feedback", "x", personal=(), endpoint=closed.url, extra={"reply": h, "thread": h}) == 409
        assert not report.send("feedback", "x", personal=(), endpoint=closed.url)
    finally:
        inbox.close()
        closed.close()
    assert report.send_status("feedback", "x", personal=(), endpoint="http://127.0.0.1:9/none", timeout=3) == 0


# -- the window -----------------------------------------------------------------------------

def _labels(card) -> list[str]:
    from PyQt6.QtWidgets import QLabel
    return [lbl.text() for lbl in card.findChildren(QLabel)]


def test_the_reply_box_shows_under_an_open_answer_keeps_what_is_typed_and_goes_once_sent():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.i18n import tr
    from dopeiptv.ui.dialogs import AnswersDialog
    app = QApplication.instance() or QApplication([])
    assert app is not None
    now = time.time()
    tickets = [{"c": "open1", "t": now - 600, "w": "Guide blank", "seen": 1,
                "r": [{"t": now - 60, "code": "needinfo", "open": True}]},
               {"c": "shut", "t": now - 900, "w": "Nice app", "seen": 1, "r": [{"t": now - 100, "code": "thanks"}]}]
    sent = []
    dlg = AnswersDialog(None, tickets, reply=lambda code, text: sent.append((code, text)))
    assert list(dlg.reply_boxes) == ["open1"]                          # under the open answer only
    assert tr("fba_reply_hint") in _labels(dlg.cards[0]) and tr("fba_reply_hint") not in _labels(dlg.cards[1])
    btn = dlg.reply_buttons["open1"]
    assert not btn.isEnabled()
    dlg.reply_boxes["open1"].setPlainText("An Xtream source")
    assert btn.isEnabled()
    # Drawn again (an answer came meanwhile): what is typed stays.
    dlg.set_tickets(tickets)
    assert dlg.reply_boxes["open1"].toPlainText() == "An Xtream source"
    dlg.reply_boxes["open1"].setPlainText("y" * (report.REPLY_MAX + 50))
    assert len(dlg.reply_boxes["open1"].toPlainText()) == report.REPLY_MAX
    dlg.reply_boxes["open1"].setPlainText("An Xtream source")
    dlg.reply_buttons["open1"].click()
    assert sent == [("open1", "An Xtream source")]
    dlg.reply_sending("open1")
    assert dlg.reply_boxes["open1"].isReadOnly() and not dlg.reply_buttons["open1"].isEnabled()
    dlg.set_tickets(tickets)                                           # still on its way
    assert dlg.reply_boxes["open1"].isReadOnly() and dlg.reply_notes["open1"].text() == tr("fba_reply_sending")
    dlg.reply_buttons["open1"].click()
    assert len(sent) == 1                                              # not twice
    dlg.reply_failed("open1", "failed")
    assert not dlg.reply_boxes["open1"].isReadOnly() and dlg.reply_buttons["open1"].isEnabled()
    assert dlg.reply_notes["open1"].text() == tr("fba_reply_failed")
    dlg.reply_failed("open1", "wait")
    assert dlg.reply_notes["open1"].text() == tr("fb_wait")
    # Sent: kept on its report, the box gone, their words in the card after the answer.
    report.add_followup(tickets[0], "An Xtream source", now)
    dlg.reply_sending("open1")
    dlg.reply_sent("open1", tickets)
    assert dlg.reply_boxes == {}
    words = _labels(dlg.cards[0])
    assert words.index("An Xtream source") > next(i for i, w in enumerate(words) if w.endswith(tr("fbr_needinfo")))
    assert any(w.startswith(tr("fba_you_replied", date="")[:6]) for w in words)
    # A modified build sends nothing: no box at all.
    again = [dict(tickets[1], c="o2", r=[{"t": now, "code": "needinfo", "open": True}])]
    assert AnswersDialog(None, again, reply=None).reply_boxes == {}
    # The site says closed: the box goes, the window says why.
    dlg3 = AnswersDialog(None, again, reply=lambda *a: None)
    assert list(dlg3.reply_boxes) == ["o2"]
    report.close_thread(again[0])
    dlg3.reply_failed("o2", "closed", again)
    assert dlg3.reply_boxes == {} and dlg3.status.text() == tr("fba_reply_closed")


def test_the_window_sends_a_reply_in_its_thread_scrubbed_and_hears_closed(monkeypatch, tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QSettings, QThreadPool
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.i18n import tr
    from dopeiptv.ui.dialogs import AnswersDialog
    from dopeiptv.ui.mw_report import _ReportMixin
    for var in _PROXIES:
        monkeypatch.delenv(var, raising=False)
    app = QApplication.instance() or QApplication([])

    class Host(_ReportMixin):
        def __init__(self, settings) -> None:
            self.settings = settings
            self.pool = QThreadPool()
            self.toasts: list[str] = []

        def _personal_values(self) -> list[str]:
            return ["Anna's IPTV"]

        def _supporter_now(self) -> bool:
            return False

        def _show_toast(self, text, ms) -> None:
            self.toasts.append(text)

    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    now = time.time()
    report.save_tickets(s, [{"c": "k1", "t": int(now - 600), "w": "Blank guide", "seen": 1,
                             "r": [{"t": int(now - 60), "code": "needinfo", "open": True}]},
                            {"c": "k2", "t": int(now - 900), "w": "Zapping", "seen": 1,
                             "r": [{"t": int(now - 30), "code": "note", "text": "Which box?", "open": True}]}])
    host = Host(s)
    inbox = Inbox()
    monkeypatch.setattr(report, "ENDPOINT", inbox.url)

    def wait(cond) -> bool:
        for _ in range(300):
            app.processEvents()
            host.pool.waitForDone(5)
            if cond():
                return True
        return False
    try:
        dlg = AnswersDialog(None, report.tickets(s), reply=host._answers_reply)
        dlg.show()
        host._answers_dialog = dlg
        assert set(dlg.reply_boxes) == {"k1", "k2"}
        dlg.reply_boxes["k1"].setPlainText("From Anna's IPTV, on SVT1")
        dlg.reply_buttons["k1"].click()
        assert wait(lambda: inbox.got and "k1" not in dlg.reply_boxes)
        body = inbox.got[-1][2]
        h = report.code_hash("k1")
        assert body["thread"] == h and body["reply"] == h and body["kind"] == "feedback"
        assert body["text"] == "From <private>, on SVT1"                 # scrubbed as feedback is
        [k1] = [t for t in report.tickets(s) if t["c"] == "k1"]
        assert k1["f"][0]["w"] == "From <private>, on SVT1" and k1["f"][0]["a"] == 1
        assert not report.can_reply(k1) and report.sent_times(s.value("feedback_sent", ""))
        assert tr("fba_reply_sent") in host.toasts
        assert "From <private>, on SVT1" in _labels(dlg.cards[0])
        # Feedback's own limits: a minute between two.
        dlg.reply_boxes["k2"].setPlainText("A Vu+")
        dlg.reply_buttons["k2"].click()
        assert len(inbox.got) == 1 and dlg.reply_notes["k2"].text() == tr("fb_wait")
        # The site says the thread is closed: closed here too, said plainly.
        s.setValue("feedback_sent", "")
        closed = Inbox(status=409)
        monkeypatch.setattr(report, "ENDPOINT", closed.url)
        dlg.reply_buttons["k2"].click()
        assert wait(lambda: closed.got and "k2" not in dlg.reply_boxes)
        [k2] = [t for t in report.tickets(s) if t["c"] == "k2"]
        assert not k2.get("f") and not k2["r"][-1].get("open")
        assert dlg.status.text() == tr("fba_reply_closed")
        closed.close()
        dlg.close()
    finally:
        inbox.close()
