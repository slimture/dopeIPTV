"""Each macOS .dmg says the oldest macOS it runs on in its Info.plist
(LSMinimumSystemVersion), so Finder refuses an older Mac instead of the app
failing there: Apple Silicon's Homebrew bottles are macOS 15's, Intel's
PyQt6 wheel needs 14. release.yml names it per build, the spec writes it,
and the README says the same. Read as text: CI installs no YAML parser."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WANT = {"arm64": "15.0", "x86_64": "14.0"}


def _legs() -> dict[str, str]:
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    job = text[text.index("\n  macos-dmg:"):]
    job = job[:job.index("\n    steps:")]
    legs = {}
    for chunk in job.split("- runner:")[1:]:
        arch = re.search(r"^\s+arch: (\S+)", chunk, re.M)
        floor = re.search(r"^\s+macos_min: '([\d.]+)'", chunk, re.M)
        assert arch, chunk
        legs[arch.group(1)] = floor.group(1) if floor else ""
    return legs


def test_each_macos_build_names_the_oldest_macos_it_runs_on():
    assert _legs() == WANT


def test_the_spec_writes_it_into_the_info_plist():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    step = text[text.index("- name: PyInstaller bundle (.app)"):]
    step = step[:step.index("run: pyinstaller")]
    assert "DOPEIPTV_MACOS_MIN: ${{ matrix.macos_min }}" in step
    spec = (ROOT / "dopeiptv-mac.spec").read_text(encoding="utf-8")
    assert re.search(r"'LSMinimumSystemVersion': os\.environ\.get\(\"DOPEIPTV_MACOS_MIN\"", spec)
    assert "'LSMinimumSystemVersion': '11.0'" not in spec


def test_the_readme_says_the_same():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"Apple Silicon[^)]*macOS (\d+) or later\).*?Intel Macs \(macOS (\d+) or later\)",
                  readme, re.S)
    assert m, "README's macOS line"
    assert (f"{m.group(1)}.0", f"{m.group(2)}.0") == (WANT["arm64"], WANT["x86_64"])
