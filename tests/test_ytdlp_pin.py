"""The yt-dlp release a build pins (tools/ytdlp_pin.py into core/ytdlp_pin.py):
only after yt-dlp's own PGP signature over SHA2-256SUMS checks out against
the key the app names, and written so the app reads back what was pinned."""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ytdlp_pin_tool", ROOT / "tools" / "ytdlp_pin.py")
tool = importlib.util.module_from_spec(spec)
sys.modules["ytdlp_pin_tool"] = tool
spec.loader.exec_module(tool)

from dopeiptv.core import ytdlp, ytdlp_pin  # noqa: E402

FPR = ytdlp_pin.KEY


def test_a_good_signature_by_the_key_and_no_other():
    good = f"[GNUPG:] NEWSIG\n[GNUPG:] GOODSIG 57CF65933B5A7581 x\n[GNUPG:] VALIDSIG {'AB' * 20} 2026-11-01 1 0 4 0 1 10 00 {FPR}\n"
    assert tool.signed_by(good, FPR)
    assert tool.signed_by(f"[GNUPG:] VALIDSIG {FPR} 2026-11-01 1 0 4 0 1 10 00 {FPR}\n", FPR)
    assert not tool.signed_by(f"[GNUPG:] VALIDSIG {'AB' * 20} 2026 1 0 4 0 1 10 00 {'CD' * 20}\n", FPR)
    assert not tool.signed_by(f"[GNUPG:] GOODSIG {FPR} x\n", FPR)
    assert not tool.signed_by("", FPR)


def test_the_pin_written_is_the_pin_read(tmp_path):
    sums = "\n".join(f"{c * 64}  {a}" for c, a in zip("abcdef0123", tool.ASSETS, strict=False))
    pin = tool.make_pin("2026.11.10", sums + "\n" + "9" * 64 + "  yt-dlp.tar.gz\n")
    assert "yt-dlp.tar.gz" not in pin["sums"] and set(pin["sums"]) <= set(tool.ASSETS)
    text = tool.module_text((ROOT / "dopeiptv/core/ytdlp_pin.py").read_text(), pin)
    out = tmp_path / "pinned.py"
    out.write_text(text)
    spec = importlib.util.spec_from_file_location("pinned", out)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.PIN == pin and mod.KEY == FPR
    assert ytdlp.pinned(mod.PIN) == ("2026.11.10", pin["sums"])
    assert "None" in tool.module_text(text, None).rsplit("=", 1)[1]


def test_a_release_without_the_files_the_app_needs_is_not_pinned():
    with pytest.raises(tool.PinError):
        tool.make_pin("2026.11.10", f"{'a' * 64}  yt-dlp_linux\n")


def test_the_committed_build_names_no_release():
    """The release is pinned by the build (release.yml), never by hand in git."""
    assert ytdlp_pin.PIN is None


@pytest.mark.skipif(shutil.which("gpg") is None or shutil.which("gpgv") is None, reason="no gpg")
def test_the_key_in_the_repository_is_the_key_the_app_names():
    key = (ROOT / "packaging" / "yt-dlp-signing-key.asc").read_bytes()
    # The right key, but a signature that is none: refused for the signature.
    with pytest.raises(tool.PinError, match="not signed"):
        tool.verify(b"x  yt-dlp\n", b"not a signature", key, FPR)
    # Any other key: refused before anything is checked.
    with pytest.raises(tool.PinError, match="is not the key"):
        tool.verify(b"x", b"x", key, "AB" * 20)
