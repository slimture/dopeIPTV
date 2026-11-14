"""Nerd for recordings: the file name template, the command after a
recording, and their checks - and a real recording from a fake channel that
lands where the template says and runs the command when done."""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("PyQt6.QtCore")

from PyQt6.QtCore import QSettings  # noqa: E402

from dopeiptv.core import nerd, rec_extras  # noqa: E402
from dopeiptv.core.nerd_check import FAIL, INFO, OK, WARN  # noqa: E402

WHEN = datetime(2026, 11, 14, 21, 5)
PY = sys.executable


@pytest.fixture
def settings(tmp_path):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("nerd_enabled", "true")
    s.setValue("recordings_dir", str(tmp_path / "rec"))
    nerd.install(s)
    yield s
    nerd.install(None)


# -- names ---------------------------------------------------------------------

@pytest.mark.parametrize("template, want", [
    ("{title} {date} {time}", "Nyheterna 2026-11-14 21.05"),
    ("{channel}/{title} {date}", os.path.join("SVT1 HD", "Nyheterna 2026-11-14")),
    ("{year}/{month}/{channel} - {title}", os.path.join("2026", "11", "SVT1 HD - Nyheterna")),
    ("../../etc/{title}", os.path.join("etc", "Nyheterna")),          # never outside
    ("/abs/{title}", os.path.join("abs", "Nyheterna")),
    ("{nothing}", "Nyheterna 2026-11-14 21.05"),                          # empty: the default
    ("", "Nyheterna 2026-11-14 21.05"),
])
def test_a_template_names_the_recording(template, want):
    assert rec_extras.render_name(template, "Nyheterna", "SVT1 HD", WHEN) == want


def test_a_title_cannot_make_folders_or_escape():
    """Only the template's own "/" makes a folder: "AC/DC Live" stays one
    file, and nothing in a title gets outside the recording folder."""
    assert rec_extras.render_name("{title}", "AC/DC Live", "", WHEN) == "AC-DC Live"
    name = rec_extras.render_name("{channel}/{title}", 'a/b\\..\\c: "d" <e>|?*', "TV/4", WHEN)
    folder, file = name.split(os.sep)
    assert folder == "TV-4" and "/" not in file and "\\" not in file
    assert ".." not in name.split(os.sep)                      # never a step up
    assert ":" not in file and '"' not in file and "<" not in file


def test_unknown_placeholders_are_named():
    assert rec_extras.unknown_fields("{title} {chanel} {date} {foo}") == ["chanel", "foo"]
    c = rec_extras.check_template("{channel}/{titel}")
    assert (c.state, c.key, c.params["names"]) == (FAIL, "nerd_ck_tpl_unknown", "{titel}")
    c = rec_extras.check_template("{channel}/{title}")
    assert c.state == OK and c.params["example"] == os.path.join("SVT1 HD", "Nyheterna") + ".ts"
    assert rec_extras.check_template("").state == INFO


def test_the_recording_manager_names_by_the_template(settings, tmp_path):
    from dopeiptv.core.recording import RecordingManager
    rm = RecordingManager(settings)
    settings.setValue("nerd_rec_template", "{channel}/{title}")
    nerd.install(settings)
    p1 = rm.build_path("Nyheterna", channel="SVT1 HD")
    assert p1 == str(tmp_path / "rec" / "SVT1 HD" / "Nyheterna.ts")
    open(p1, "wb").close()
    assert rm.build_path("Nyheterna", channel="SVT1 HD").endswith("Nyheterna (1).ts")
    # Nerd off: the name as it always was.
    settings.setValue("nerd_enabled", "false")
    nerd.install(settings)
    p = rm.build_path("Nyheterna", channel="SVT1 HD")
    assert os.path.dirname(p) == str(tmp_path / "rec")
    assert os.path.basename(p).startswith("Nyheterna 20")


# -- the command after -----------------------------------------------------------

def _marker_cmd() -> str:
    code = "import sys,pathlib; pathlib.Path(sys.argv[1]+'.done').write_text(sys.argv[2]+'|'+sys.argv[3])"
    return f'"{PY}" -c "{code}" "{{path}}" "{{title}}" "{{channel}}"'


def test_placeholders_are_filled_per_argument_never_through_a_shell(tmp_path):
    argv = rec_extras.post_argv('echo "{title}" {file}', "/r/a b.ts", 'x"; rm -rf ~; "', "C")
    assert argv == ["echo", 'x"; rm -rf ~; "', "a b.ts"]
    with pytest.raises(ValueError):
        rec_extras.post_argv('echo "unclosed', "/r/a.ts", "t")


def test_the_command_runs_for_a_finished_recording(tmp_path):
    rec = tmp_path / "Nyheterna.ts"
    rec.write_bytes(b"\x47" * 188)
    proc = rec_extras.run_post(_marker_cmd(), str(rec), "Nyheterna", "SVT1 HD")
    assert proc is not None and proc.wait(20) == 0
    assert (tmp_path / "Nyheterna.ts.done").read_text() == "Nyheterna|SVT1 HD"
    assert rec_extras.run_post("", str(rec), "t") is None
    assert rec_extras.run_post("/no/such/program {path}", str(rec), "t") is None


def test_the_command_check_says_what_happened(tmp_path):
    c = rec_extras.check_post(_marker_cmd())
    assert (c.state, c.key) == (OK, "nerd_ck_post_ok") and "Exit code: 0" in c.detail
    c = rec_extras.check_post(f'"{PY}" -c "import sys; print(\'nope\', file=sys.stderr); sys.exit(3)"')
    assert (c.state, c.key, c.params["code"]) == (FAIL, "nerd_ck_post_code", 3)
    assert "nope" in c.detail
    c = rec_extras.check_post("/no/such/program {path}")
    assert (c.state, c.key) == (FAIL, "nerd_ck_post_missing")
    c = rec_extras.check_post(f'"{PY}" -c "import time; time.sleep(5)"', timeout=0.5)
    assert (c.state, c.key) == (WARN, "nerd_ck_post_slow")


def test_the_folder_check(tmp_path):
    c = rec_extras.check_folder(str(tmp_path))
    assert c.state in (OK, WARN) and "Writing a test file: ok" in c.detail
    assert not [n for n in os.listdir(tmp_path) if n.startswith(".dopeiptv-write-test-")]
    assert rec_extras.check_folder(str(tmp_path / "nope")).key == "nerd_ck_folder_missing"
    if os.name != "nt" and os.geteuid() != 0:
        ro = tmp_path / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        try:
            assert rec_extras.check_folder(str(ro)).key == "nerd_ck_rec_readonly"
        finally:
            ro.chmod(0o700)


# -- a real recording -------------------------------------------------------------

class Channel:
    """A live channel that sends the test clip over and over."""

    def __init__(self):
        from tests.test_nerd import TS

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.end_headers()
                try:
                    for _ in range(40):
                        self.wfile.write(TS)
                        time.sleep(0.1)
                except OSError:
                    pass
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/live/1.ts"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_a_real_recording_lands_by_the_template_and_runs_the_command(settings, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.core.recording import RecordingManager
    app = QApplication.instance() or QApplication([])
    kind, _exe = RecordingManager.recorder()
    if kind is None:
        pytest.skip("no ffmpeg or mpv to record with")
    for var in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    settings.setValue("nerd_rec_template", "{channel}/{title}")
    settings.setValue("nerd_rec_post", _marker_cmd())
    nerd.install(settings)
    ch = Channel()
    try:
        rm = RecordingManager(settings)
        job = rm.add_job(ch.url, "Nyheterna", time.time(), time.time() + 3, channel="SVT1 HD")
        end = time.time() + 30
        while job["status"] in ("scheduled", "recording") and time.time() < end:
            app.processEvents()
            rm.tick()
            time.sleep(0.2)
        assert job["status"] == "done", job
        path = job["path"]
        assert path == str(tmp_path / "rec" / "SVT1 HD" / "Nyheterna.ts")
        assert os.path.getsize(path) > 0
        marker = path + ".done"
        end = time.time() + 20
        while not os.path.exists(marker) and time.time() < end:
            time.sleep(0.1)
        assert open(marker).read() == "Nyheterna|SVT1 HD"
    finally:
        ch.close()
