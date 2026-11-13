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


def test_wrapped_lines_are_joined_and_blocks_kept_apart():
    # GitHub shows a release's every line break: notes wrapped at 72
    # columns broke mid-sentence on a phone.
    text = ("**One headline\nover two lines.**\n\n### The big ones\n\n- **An item** that\n  goes on here.\n"
            "  - a sub item\n- Another\n\n<details>\n<summary>More</summary>\n\nA paragraph\nwrapped.\n\n"
            "```\nkeep\nas is\n```\nHard break  \nstays.\n\n| a | b |\n| - | - |\n</details>")
    got = tidy_mod.unwrap(text)
    assert "**One headline over two lines.**" in got
    assert "- **An item** that goes on here.\n  - a sub item\n- Another" in got
    assert "<details>\n<summary>More</summary>\n\nA paragraph wrapped." in got
    assert "```\nkeep\nas is\n```" in got and "Hard break  \nstays." in got
    assert "| a | b |\n| - | - |\n</details>" in got
    assert tidy_mod.unwrap(got) == got


def test_a_release_already_short_still_gets_its_lines_joined():
    got = tidy(_rel("v1.4.5", "## dopeIPTV 1.4.5\n\nA line\nwrapped here.\n"), "v1.4.5")
    assert got == "## dopeIPTV 1.4.5\n\nA line wrapped here.\n"
    assert tidy(_rel("v1.4.5", got), "v1.4.5") is None


def test_release_notes_are_written_one_line_a_paragraph():
    # RELEASE_NOTES.md becomes the release's text as it is.
    notes = (Path(__file__).resolve().parent.parent / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert tidy_mod.unwrap(notes.strip()) == notes.strip()
