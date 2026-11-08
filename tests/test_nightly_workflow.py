"""The nightly workflow builds with the release workflow and publishes a
pre-release the app recognises - and a release is not changed by it. Read
as text: CI installs no YAML parser."""
from __future__ import annotations

from pathlib import Path

from dopeiptv.core.updates import NIGHTLY_PREFIX

ROOT = Path(__file__).resolve().parent.parent
RELEASE = (ROOT / ".github/workflows/release.yml").read_text()
NIGHTLY = (ROOT / ".github/workflows/nightly.yml").read_text()


def test_the_release_workflow_can_be_called_with_a_nightly_version():
    assert "workflow_call:" in RELEASE and "nightly_version:" in RELEASE
    # Every job stamps it right after checkout, and only for a nightly.
    stamp = ("      - uses: actions/checkout@v5\n"
             "      - name: Nightly version\n"
             "        if: ${{ inputs.nightly_version }}\n")
    assert RELEASE.count(stamp) == RELEASE.count("actions/checkout@v5") == 5


def test_a_release_still_attaches_only_on_a_tag():
    attaches = RELEASE.count("- name: Attach to release")
    guarded = RELEASE.count("- name: Attach to release\n"
                            "        if: startsWith(github.ref, 'refs/tags/')")
    assert attaches == guarded == 5


def test_nightly_publishes_a_pre_release_the_app_reads():
    assert "uses: ./.github/workflows/release.yml" in NIGHTLY
    assert "secrets: inherit" in NIGHTLY
    assert f'gh release create "{NIGHTLY_PREFIX}${{V}}"' in NIGHTLY
    assert "--prerelease" in NIGHTLY and "--latest=false" in NIGHTLY
    assert "branches: [testing]" in NIGHTLY
    # Never a v-tag: release.yml runs on those.
    assert not NIGHTLY_PREFIX.startswith("v")


def test_a_test_build_publishes_what_built_and_a_release_fails_loud():
    # Every job of release.yml may fail without failing a test build (a
    # download that times out sends no failure mail) - never a release.
    expr = "(inputs.nightly_version || '') != ''"
    assert RELEASE.count(f"continue-on-error: ${{{{ {expr} }}}}") == 3
    assert f"continue-on-error: ${{{{ matrix.experimental || {expr} }}}}" \
        in RELEASE
    # What did not build is named in the notes; no AppImage, no nightly.
    assert "missing.txt" in NIGHTLY and "Not in this build" in NIGHTLY
    assert NIGHTLY.count("if: steps.gather.outputs.publish == 'true'") == 3
