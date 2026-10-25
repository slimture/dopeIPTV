"""The About dialog and its update check. The answer arrives after the
dialog is up (the check is async). In 1.2.17 the release notes then needed
the dialog to grow, which on Wayland it did not: the notes box lay over
"Update now", the links and the TMDB line. The room is now there from the
start, so the answer never changes the dialog's size - and the title said
"About dopeIPTV dopeIPTV"."""
import os

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QMainWindow  # noqa: E402

from dopeiptv.ui import mw_settings  # noqa: E402


class _Host(QMainWindow):
    _format_release_notes = staticmethod(
        mw_settings._SettingsMixin._format_release_notes)

    def __init__(self) -> None:
        super().__init__()
        self.pool = None

    def _update_action_label(self) -> str:
        return "Update now"

    def _update_action(self) -> None:
        pass


def _notes() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "RELEASE_NOTES.md"), encoding="utf-8") as fh:
        return fh.read()


def _rows(dialog: QDialog) -> list:
    """The dialog's rows top to bottom, as (name, rect), shown ones only."""
    lay = dialog.layout()
    out = []
    for i in range(lay.count()):
        item = lay.itemAt(i)
        w = item.widget()
        if w is not None:
            if w.isVisible():
                out.append((type(w).__name__, w.geometry()))
            continue
        sub = item.layout()
        if any(sub.itemAt(j).widget() is not None
               and sub.itemAt(j).widget().isVisible()
               for j in range(sub.count())):
            out.append(("row", item.geometry()))
    return out


def _open_about(monkeypatch, answer) -> dict:
    app = QApplication.instance() or QApplication([])
    pending = []
    monkeypatch.setattr(mw_settings, "run_async",
                        lambda pool, fn, done, fail: pending.append(done))
    seen: dict = {}

    def fake_exec(dialog):
        dialog.show()
        app.processEvents()
        seen["size_before"] = dialog.size()
        pending[-1](answer)
        for _ in range(5):
            app.processEvents()
        seen["size_after"] = dialog.size()
        seen["title"] = dialog.windowTitle()
        seen["rows"] = _rows(dialog)
        seen["height"] = dialog.height()
        seen["labels"] = [
            (lbl.text()[:30], lbl.height(), lbl.heightForWidth(lbl.width()))
            for lbl in dialog.findChildren(QLabel)
            if lbl.isVisible() and lbl.wordWrap()]
        dialog.close()
        return 0

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    mw_settings._SettingsMixin.show_about(_Host())
    return seen


@pytest.mark.parametrize("tag", ["v99.0.0", "v0.0.1"],
                         ids=["update-waiting", "up-to-date"])
def test_the_answer_never_resizes_or_overlaps(monkeypatch, tag):
    seen = _open_about(monkeypatch, {"tag": tag, "body": _notes(),
                                     "url": "https://example.invalid"})
    assert seen["title"].count("dopeIPTV") == 1
    # What broke on Wayland: a dialog that had to grow after it was up.
    assert seen["size_after"] == seen["size_before"]
    rows = seen["rows"]
    assert "QTextBrowser" in [n for n, _ in rows]   # the notes are shown
    for (a, ra), (b, rb) in zip(rows, rows[1:], strict=False):
        assert ra.bottom() < rb.top(), f"{a} {ra} runs into {b} {rb}"
    assert rows[-1][1].bottom() < seen["height"]
    for text, h, need in seen["labels"]:
        assert h >= need, f"{text!r} is cut: {h}px of {need}px"


def test_a_failed_check_leaves_no_overlap(monkeypatch):
    seen = _open_about(monkeypatch, None)
    rows = seen["rows"]
    assert "QTextBrowser" not in [n for n, _ in rows]
    for (a, ra), (b, rb) in zip(rows, rows[1:], strict=False):
        assert ra.bottom() < rb.top(), f"{a} {ra} runs into {b} {rb}"
