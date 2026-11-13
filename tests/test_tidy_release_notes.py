"""tools/tidy_release_notes.py: the releases page short, nothing lost."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "tidy_release_notes", Path(__file__).resolve().parent.parent / "tools" / "tidy_release_notes.py")
assert _spec and _spec.loader
tidy_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tidy_mod)
tidy = tidy_mod.tidy

LONG = "\n".join(f"- change {i}" for i in range(12))
BODY = (f"## dopeIPTV 1.4.5\n\n**The big one.**\n\n{LONG}\n\n"
        f"## dopeIPTV 1.4.4\n\nSmoother playback.\n\n{LONG}\n\n"
        f"## dopeIPTV 1.4.3\n\n{LONG}\n")


def _rel(tag: str, body: str, prerelease: bool = False) -> dict:
    return {"tag_name": tag, "body": body, "prerelease": prerelease, "draft": False}


def test_the_latest_release_keeps_its_own_notes_only():
    got = tidy(_rel("v1.4.5", BODY), "v1.4.5")
    assert got.startswith("## dopeIPTV 1.4.5") and "**The big one.**" in got
    assert "1.4.4" not in got and "<details>" not in got
    assert tidy(_rel("v1.4.5", got), "v1.4.5") is None


def test_an_older_release_folds_its_notes_under_its_summary():
    body = BODY.split("## dopeIPTV 1.4.4", 1)[1]
    got = tidy(_rel("v1.4.4", "## dopeIPTV 1.4.4" + body), "v1.4.5")
    assert got.startswith("Smoother playback.\n\n<details>\n<summary>What's new in 1.4.4</summary>")
    assert "- change 11" in got and "1.4.3" not in got
    assert got.rstrip().endswith(tidy_mod.FOOTER)
    assert tidy(_rel("v1.4.4", got), "v1.4.5") is None


def test_one_fold_is_enough():
    # An old release that folded its own long part keeps one fold only.
    body = "## dopeIPTV 0.7.3\n\n**Pop-out.**\n\n<details>\n<summary><b>Full notes</b></summary>\n\n" + LONG + "\n</details>\n"
    got = tidy(_rel("v0.7.3", body), "v1.4.5")
    assert got.count("<details>") == 1 and "Full notes" not in got and got.startswith("**Pop-out.**")


def test_a_short_release_stays_as_it_is():
    assert tidy(_rel("v0.4.3", "Fixes the login."), "v1.4.5") is None
    assert tidy(_rel("v0.5.0", ""), "v1.4.5") is None


def test_a_test_build_folds_its_changes():
    body = ("## dopeIPTV 1.4.5.90 (test build)\n\nBuilt from `testing`.\n\n"
            "### Changes since v1.4.4\n\n" + LONG + "\n")
    got = tidy(_rel("nightly-1.4.5.90", body, prerelease=True), "v1.4.5")
    assert "### Changes since v1.4.4\n\n<details>\n<summary>12 changes</summary>" in got
    assert "- change 0" in got and tidy(_rel("nightly-1.4.5.90", got, prerelease=True), "v1.4.5") is None
    # The admin page's Builds tab still finds the changes ("- " lines).
    assert sum(1 for line in got.splitlines() if line.startswith("- ")) == 12


def test_the_app_still_reads_a_folded_body():
    # The update dialog strips the folds (mw_settings._format_release_notes).
    import re
    got = tidy(_rel("v1.4.4", "## dopeIPTV 1.4.4\n\nSmoother playback.\n\n" + LONG), "v1.4.5")
    plain = re.sub(r"(?i)</?details>", "", re.sub(r"(?is)<summary>.*?</summary>", "", got))
    assert "Smoother playback." in plain and "- change 5" in plain and "<" not in plain
