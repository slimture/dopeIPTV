"""The nightly workflow builds with the release workflow and publishes a
pre-release the app recognises - and a release is not changed by it. Read
as text: CI installs no YAML parser."""
from __future__ import annotations

import re
from pathlib import Path

from dopeiptv.core.updates import NIGHTLY_PREFIX

ROOT = Path(__file__).resolve().parent.parent
RELEASE = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
NIGHTLY = (ROOT / ".github/workflows/nightly.yml").read_text(encoding="utf-8")


def test_the_release_workflow_can_be_called_with_a_nightly_version():
    assert "workflow_call:" in RELEASE and "nightly_version:" in RELEASE
    # Every job stamps it right after checkout (the action by its commit,
    # its token not left in the clone), and only for a nightly.
    checkouts = re.findall(r"      - uses: (actions/checkout@[0-9a-f]{40}   # v[\d.]+)\n", RELEASE)
    assert len(set(checkouts)) == 1
    # Between the two, the yt-dlp release the ytdlp-pin job named.
    pin = re.search(r"      - name: The yt-dlp release this build names \(the ytdlp-pin job\)\n"
                    r"        uses: actions/download-artifact@[0-9a-f]{40}   # v[\d.]+\n"
                    r"        with:\n          name: ytdlp-pin\n          path: dopeiptv/core\n", RELEASE)
    assert pin is not None
    stamp = (f"      - uses: {checkouts[0]}\n"
             "        with:\n"
             "          persist-credentials: false\n"
             + pin.group(0) +
             "      - name: Nightly version\n"
             "        if: ${{ inputs.nightly_version }}\n")
    assert RELEASE.count(stamp) == 5
    # The other two checkouts are the checksums job's and the ytdlp-pin
    # job's, which build nothing.
    assert RELEASE.count("uses: actions/checkout@") == len(checkouts) == 7


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
    # Built from testing: on its pushes, or by hand while work lands often.
    assert "branches: [testing]" in NIGHTLY or "workflow_dispatch:" in NIGHTLY
    # Never a v-tag: release.yml runs on those.
    assert not NIGHTLY_PREFIX.startswith("v")


def test_a_test_build_publishes_what_built_and_a_release_fails_loud():
    # Every job of release.yml may fail without failing a test build (a
    # download that times out sends no failure mail) - never a release.
    expr = "(inputs.nightly_version || '') != ''"
    assert RELEASE.count(f"continue-on-error: ${{{{ {expr} }}}}") == 4
    assert f"continue-on-error: ${{{{ matrix.experimental || {expr} }}}}" \
        in RELEASE
    # What did not build is named in the notes; no AppImage, no nightly.
    assert "missing.txt" in NIGHTLY and "Not in this build" in NIGHTLY
    # Gather's publish guard on the signed checksums, the notes, the
    # pre-release and keeping the newest seven.
    assert NIGHTLY.count("if: steps.gather.outputs.publish == 'true'") == 4


def test_a_release_fails_loud_when_its_flatpak_does_not_build_or_start():
    # #36: the leg was allowed to fail, and every start of 1.5.0 to 1.6.5
    # crashed. Only a test build may go on without it.
    job = RELEASE.split("\n  flatpak:\n", 1)[1].split("\n  # ----", 1)[0]
    assert "continue-on-error: ${{ (inputs.nightly_version || '') != '' }}" in job
    assert "continue-on-error: true\n    container:" not in job
    # The SDK installed over the image's own copy on a filesystem that can.
    assert "volumes:\n        - /var/lib/flatpak" in job
    # Installed, then run in the runtime the users get: the self-check and
    # a start on a virtual screen, both failing the job.
    smoke = job.split("Smoke test - install the Flatpak", 1)[1]
    assert '"$APP" --self-check' in smoke
    assert 'flatpak run "$APP" >/tmp/flatpak-start.log' in smoke
    assert "did not keep running for 15 s" in smoke and "exit 1" in smoke


def test_a_macos_rebuild_replaces_only_the_dmg_of_the_same_app():
    # workflow_dispatch with replace_macos_dmg: the other jobs stay out, the
    # app's code must be the tag's, and only a signed .dmg of the tag's
    # version replaces the one on the release.
    assert RELEASE.count("replace_macos_dmg:") == 2
    skip = "    if: ${{ (inputs.replace_macos_dmg || '') == '' }}\n"
    assert RELEASE.count(skip) == 4
    assert 'git diff --quiet "$TAG" HEAD -- dopeiptv requirements.txt' in RELEASE
    # The .dmg, and the checksum list signed again after it (checksums job).
    assert RELEASE.count('gh release upload "$TAG"') == 2
    assert 'if [ "$TAG" != "v${V}" ]; then' in RELEASE
