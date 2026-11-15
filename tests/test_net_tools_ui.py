"""The network tools in the window: a source's backup addresses in its Nerd
options (a bad line refused, the one in use said), Test connection's window
(each step as it goes, a copy without the login), the connections to the
account counted and warned about before one more - and the account details
saying how many are in use."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.core import connections, mirrors, nerd  # noqa: E402
from dopeiptv.i18n import tr  # noqa: E402


class _S(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def nerd_on(app, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")
    s = _S(nerd_enabled="true")
    nerd.install(s)
    yield s
    nerd.install(None)


def _wait(cond, secs=20.0) -> bool:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


# -- backup addresses on the source's page --------------------------------------------

def test_the_backup_addresses_rows_follow_the_kind(nerd_on):
    from tests.test_source_page import shown, source_page, tab_of
    d = source_page({"id": "pl1", "name": "IPTV", "kind": "xtream",
                     "server": "http://main.example.com", "username": "u", "password": "p",
                     "options": {mirrors.KEY: "b.example.com:8080"}})
    p = d.nerd_panel
    assert tab_of(d, p.mirrors_box) == "net" and shown(d, p.mirrors_box) and shown(d, p.mr_line)
    assert d.test_btn is not None
    assert d.values()["options"][mirrors.KEY] == "b.example.com:8080"
    d.kind.setCurrentIndex(d.kind.findData("enigma2"))
    assert not shown(d, p.mirrors_box) and d.test_btn is not None
    assert mirrors.KEY not in p.values()
    d.kind.setCurrentIndex(d.kind.findData("m3u"))
    assert shown(d, p.mirrors_box)
    assert p.mirrors_box.placeholderText().endswith(".m3u")


def test_the_line_says_which_address_is_in_use(nerd_on):
    from tests.test_source_page import source_page
    nerd_on[mirrors.pick_key("pl1")] = "http://b.example.com:8080"
    d = source_page({"id": "pl1", "name": "IPTV", "kind": "xtream",
                     "server": "http://main.example.com", "username": "u", "password": "p",
                     "options": {mirrors.KEY: "b.example.com:8080\nc.example.com"}})
    line = d.nerd_panel.mr_line
    line.show_preview()
    assert line.result.key == "nerd_ck_mr_list"
    assert line.text.text() == tr("nerd_ck_mr_list", host="http://b.example.com:8080", n=2)
    assert d.nerd_panel.test_source()["id"] == "pl1"


def test_a_bad_line_keeps_the_page_open(nerd_on, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from tests.test_source_page import source_page
    said: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _p, _t, text, *a, **k: said.append(text))
    backs: list[int] = []
    d = source_page({"id": "pl1", "name": "IPTV", "kind": "xtream",
                     "server": "http://main.example.com", "username": "u", "password": "p",
                     "options": {}}, back=lambda: backs.append(1))
    d.nerd_panel.mirrors_box.setPlainText("ok.example.com\nrtmp://wrong.example.com")
    d.go_back()
    assert said == [tr("nerd_ck_mr_scheme", n=2)] and not backs
    assert d.current_tab() == "net"
    d.nerd_panel.mirrors_box.setPlainText("ok.example.com")
    d.go_back()
    assert backs == [1]


# -- Test connection's window ------------------------------------------------------------

def test_the_test_window_shows_each_step_and_copies_no_login(nerd_on):
    from dopeiptv.providers.client import make_client
    from dopeiptv.ui import conn_test_dialog as ctd
    from tests.test_conn_test import Xtream
    srv = Xtream()
    try:
        src = {"id": "pl1", "name": "My Provider", "kind": "xtream", "server": srv.url,
               "username": "alice", "password": "s3cretpw"}
        d = ctd.ConnTestDialog(None, src, nerd_on, make_client, speed_secs=0.3, autostart=False)
        d.start()
        assert d.close_btn.text() == tr("ct_stop") and not d.again_btn.isEnabled()
        assert _wait(lambda: d.test is not None and d.test.finished and not d._timer.isActive())
        marks = {name: row.mark.text() for name, row in d.rows.items()}
        assert marks == {"dns": "✓", "tcp": "✓", "tls": "–", "login": "✓", "stream": "✓",
                         "speed": "✓"}
        assert tr("conn_in_use", n=1, max=2) in d.rows["login"].more.text()
        assert d.again_btn.isEnabled() and d.close_btn.text() == tr("common_close")
        d.copy()
        text = QApplication.clipboard().text()
        assert "s3cretpw" not in text and "alice" not in text and "My Provider" not in text
        assert tr("ct_step_speed") in text and "Mbit/s" in text
        d.done(0)
    finally:
        srv.close()


def test_stop_ends_a_test_under_way(nerd_on):
    from dopeiptv.providers.client import make_client
    from dopeiptv.ui import conn_test_dialog as ctd
    from tests.test_conn_test import Xtream
    srv = Xtream(rate=100_000)
    try:
        src = {"kind": "xtream", "server": srv.url, "username": "a", "password": "b"}
        d = ctd.ConnTestDialog(None, src, None, make_client, speed_secs=30, autostart=False)
        d.start()
        assert _wait(lambda: d.test.snapshot()[-1].state == "run")
        d._close_or_stop()
        assert _wait(lambda: d.test.finished, 10)
        assert _wait(lambda: d.rows["speed"].result.text() == tr("ct_skip_cancel"), 5)
        assert d.close_btn.text() == tr("common_close") and d.again_btn.isEnabled()
        d.done(0)
    finally:
        srv.close()


# -- connections to the account ---------------------------------------------------------

class _Dvr:
    def __init__(self, streaming=True, recording=False):
        self.streaming, self.recording, self.closed = streaming, recording, False


class _Pool:
    def __init__(self, sessions):
        self.sessions = sessions


class _Player:
    def __init__(self, url=None, dvr=None, pool=()):
        self.current_url = url
        self._dvr = dvr
        self._dvr_pool = _Pool(list(pool)) if pool else None


class _Cell:
    def __init__(self, url=None, source=""):
        self.url, self.source_id = url, source


class _Win:
    def __init__(self, cells):
        self.cells = cells
        self._focused = cells[0] if cells else None


class _Rec:
    def __init__(self, jobs):
        self.jobs = jobs


class _Store:
    active_id = "pl1"


def _window(limit=None, player=None, jobs=(), cells=None, mv_stop="true"):
    from dopeiptv.ui.mw_connections import _ConnectionsMixin
    from dopeiptv.ui.mw_recording import _RecordingMixin

    class W(_ConnectionsMixin, _RecordingMixin):
        def __init__(self):
            self.client = type("C", (), {"max_connections": limit})()
            self.player = player
            self.rec = _Rec(list(jobs))
            self._multiview_win = _Win(cells) if cells is not None else None
            self.playlist_store = _Store()
            self.settings = _S(mv_stop_docked=mv_stop)
            self._failover_source = None
            self.asked: list[str] = []
            self.answer = 0

        def _choice_dialog(self, title, message, options, dont_ask_setting=None):
            self.asked.append(message)
            return self.answer
    return W()


def test_the_apps_own_connections_are_counted():
    dvr = _Dvr()
    kept = _Dvr(recording=True)
    w = _window(player=_Player("http://h.example.com/live/u/p/1.ts", dvr, [dvr, kept, _Dvr(streaming=False)]),
                jobs=[{"status": "recording", "url": "http://h.example.com/live/u/p/2.ts", "source": "pl1"},
                      {"status": "recording", "url": "http://h.example.com/live/u/p/3.ts",
                       "inplayer": True},
                      {"status": "recording", "url": "http://h.example.com/x", "recorder": object()},
                      {"status": "scheduled", "url": "http://h.example.com/x"},
                      {"status": "recording", "url": "http://other.example.com/x", "source": "pl2"}],
                cells=[_Cell("http://h.example.com/live/u/p/4.ts", "pl1"), _Cell(None),
                       _Cell("/home/me/film.mkv")])
    kinds = sorted(u.kind for u in w._conn_uses())
    assert kinds == ["buffer", "buffer", "multiview", "recording", "recording"]
    assert connections.own_count(w._conn_uses(), "pl1") == 4
    # Let go: the player's buffer goes, unless a recording reads it.
    assert sorted(u.kind for u in w._conn_uses(free_player=True)) == [
        "buffer", "multiview", "recording", "recording"]
    direct = _window(player=_Player("http://h.example.com/live/u/p/1.ts"))
    assert [u.kind for u in direct._conn_uses()] == ["player"]
    local = _window(player=_Player("/home/me/film.mkv"))
    assert local._conn_uses() == []


def test_a_multiview_cell_over_the_limit_is_asked_about():
    playing = _Player("http://h.example.com/live/u/p/1.ts")
    w = _window(limit=2, player=playing, cells=[_Cell("http://h.example.com/a.ts", "pl1"), _Cell()],
                mv_stop="false")
    w.answer = 1                                   # Cancel
    assert not w._conn_ok_multiview(None, "http://h.example.com/b.ts")
    assert len(w.asked) == 1 and tr("conn_what_multiview") in w.asked[0]
    assert tr("conn_over", max=2, n=2) in w.asked[0]
    assert tr("conn_k_player") in w.asked[0] and tr("conn_k_multiview") in w.asked[0]
    w.answer = 0                                   # Go ahead anyway
    assert w._conn_ok_multiview(None, "http://h.example.com/b.ts")
    # The docked player lets go first (Settings): room for it.
    w.settings["mv_stop_docked"] = "true"
    w.asked.clear()
    assert w._conn_ok_multiview(None, "http://h.example.com/b.ts") and not w.asked
    # A cell that plays gives its stream up for the new one.
    w.settings["mv_stop_docked"] = "false"
    assert w._conn_ok_multiview(0, "http://h.example.com/b.ts") and not w.asked
    # A file takes no connection; no limit known: nothing said.
    assert w._conn_ok_multiview(None, "/home/me/film.mkv") and not w.asked
    w.client.max_connections = None
    assert w._conn_ok_multiview(None, "http://h.example.com/b.ts") and not w.asked


def test_another_device_counts_while_the_providers_answer_is_fresh():
    w = _window(limit=None, player=_Player("http://h.example.com/live/u/p/1.ts"))
    w._conn_seen("pl1", {"user_info": {"max_connections": "3", "active_cons": "3"}}, own=1)
    v = w._conn_verdict(w._conn_uses())
    assert v is not None and (v.limit, v.own, v.others, v.after) == (3, 1, 2, 4)
    assert tr("conn_k_other") in w._conn_words(v)
    assert tr("conn_times", what=tr("conn_k_other"), n=2) in w._conn_words(v)


def test_the_recording_questions_carry_the_warning_when_over():
    w = _window(limit=2, player=_Player("http://h.example.com/live/u/p/1.ts"),
                jobs=[{"status": "recording", "url": "http://h.example.com/2.ts", "source": "pl1"}])
    v = w._one_more_over()
    assert v is not None and v.after == 3
    body = w._with_conn_warning("Body.", v)
    assert body.startswith("Body.\n\n") and tr("conn_risk") in body
    assert w._one_more_over(free_player=True) is None        # 1 + 1 of 2
    roomy = _window(limit=5, player=_Player("http://h.example.com/live/u/p/1.ts"))
    assert roomy._one_more_over() is None and roomy._with_conn_warning("B", None) == "B"


def test_account_details_say_how_many_are_in_use():
    from dopeiptv.ui.mw_settings import _SettingsMixin
    text = _SettingsMixin._format_account(None, {"user_info": {
        "status": "Active", "exp_date": None, "max_connections": "2", "active_cons": "1"}})
    assert tr("conn_in_use", n=1, max=2) in text
    only = _SettingsMixin._format_account(None, {"user_info": {"status": "Active", "active_cons": "1"}})
    assert tr("conn_in_use_only", n=1) in only


# -- a stream's server down: the next address ------------------------------------------------

def test_a_stream_found_on_the_next_address_starts_again():
    from dopeiptv.ui.mw_mirrors import _MirrorsMixin

    class W(_MirrorsMixin):
        def __init__(self):
            self.client = object()
            self._last_playback = {"key": "k1", "kind": "live"}
            self.player = _Player(None)
            self.toasts: list[str] = []
            self.retried = 0
            self._mirror_for = ("k1", self.client)

        def _show_toast(self, text, ms=0):
            self.toasts.append(text)

        def _retry_last_stream(self):
            self.retried += 1
    w = W()
    w._mirror_found("http://b.example.com:8080")
    assert w.retried == 1 and w.toasts == [tr("mirror_switched", host="http://b.example.com:8080")]
    assert w._stream_retries == 0 and w._diag_shown is False
    w._last_playback = {"key": "k2", "kind": "live"}
    w._mirror_found("http://b.example.com:8080")
    assert w.retried == 1                           # another channel since
    w._mirror_found(None)
    assert w.retried == 1
