"""A subtitle translated from Arabic or Hebrew can keep right-to-left marks
around every line, which put a Swedish line's full stop in front (".Hej
då"). They are taken out of lines with no right-to-left letter, and kept
where there is one."""
import os

import pytest

from dopeiptv.media.subtitle_text import fixed_bytes, strip_stray_marks

RLE, PDF, RLI, PDI, RLM = "‫", "‬", "⁧", "⁩", "‏"
CUE = "1\n00:00:01,000 --> 00:00:02,000\n"


def test_marks_leave_lines_without_rtl_letters():
    text = f"{CUE}{RLE}Hej då.{PDF}\n{RLI}Är du klar?{PDI}\n"
    assert strip_stray_marks(text) == f"{CUE}Hej då.\nÄr du klar?\n"


def test_arabic_and_hebrew_lines_keep_theirs():
    arabic = f"{RLE}مرحبا.{PDF}\n"
    hebrew = f"{RLM}שלום.\n"
    text = arabic + f"{RLE}Hello.{PDF}\n" + hebrew
    assert strip_stray_marks(text) == arabic + "Hello.\n" + hebrew


def test_text_without_marks_is_untouched():
    text = "Hej då.\r\nÄr du klar?\r\n"
    assert strip_stray_marks(text) is text


def test_fixed_bytes_keeps_the_files_encoding():
    srt = f"{CUE}{RLE}Hej då.{PDF}\n"
    clean = f"{CUE}Hej då.\n"
    assert fixed_bytes(srt.encode("utf-8")) == clean.encode("utf-8")
    bom = fixed_bytes(srt.encode("utf-8-sig"))
    assert bom.startswith(b"\xef\xbb\xbf") and bom.decode("utf-8-sig") == clean
    assert fixed_bytes(srt.encode("utf-16")).decode("utf-16") == clean
    # Nothing to take out, or not a Unicode file: left alone.
    assert fixed_bytes(clean.encode("utf-8")) is None
    assert fixed_bytes("Hej då.\n".encode("cp1252")) is None


def test_the_player_loads_a_fixed_copy_under_the_same_name(
        tmp_path, monkeypatch):
    pytest.importorskip("PyQt6")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from dopeiptv.media.embedded import _subtitle_to_load
    src = tmp_path / "Show.S01E01.sv.srt"
    src.write_text(f"{CUE}{RLE}Hej då.{PDF}\n", encoding="utf-8")
    loaded = _subtitle_to_load(str(src))
    assert loaded != str(src)
    # The same name, so mpv still reads the language from it.
    assert os.path.basename(loaded) == "Show.S01E01.sv.srt"
    with open(loaded, encoding="utf-8") as fh:
        assert fh.read() == f"{CUE}Hej då.\n"
    assert RLE in src.read_text(encoding="utf-8")       # the original stays
    plain = tmp_path / "plain.srt"
    plain.write_text(f"{CUE}Hej.\n", encoding="utf-8")
    assert _subtitle_to_load(str(plain)) == str(plain)
    missing = str(tmp_path / "missing.srt")
    assert _subtitle_to_load(missing) == missing
