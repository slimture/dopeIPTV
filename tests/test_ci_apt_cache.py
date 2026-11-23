"""CI's system packages kept between runs (.github/workflows/ci.yml): a kept
package is installed only when apt's signed lists name it with its SHA-256,
and only the test job keeps any."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
CI = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")


def _install_script() -> str:
    block = CI.split("      - name: System libraries for the PyQt6 import\n"
                     "        run: |\n", 1)[1]
    block = block.split("      - name: Install dependencies", 1)[0]
    return "\n".join(line[10:] for line in block.rstrip("\n").split("\n"))


def _check_loop() -> str:
    script = _install_script()
    start = script.index("kept=0 dropped=0")
    end = script.index('echo "Packages kept')
    return script[start:end]


def test_every_action_in_ci_is_pinned_and_the_cache_is_the_test_jobs_alone():
    uses = re.findall(r"uses: (\S+)(.*)", CI)
    assert uses
    for ref, rest in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), ref
        assert re.match(r"\s+# v\d", rest), ref
    assert CI.count("uses: actions/cache@") == 1
    key = re.search(r"key: (apt-.*)", CI).group(1)
    assert "steps.image.outputs.name" in key and "hashFiles('.github/workflows/ci.yml')" in key
    assert "ImageOS" in CI and "ImageVersion" in CI
    # The builds take their packages from the mirror, never from a cache.
    for other in WORKFLOWS.glob("*.yml"):
        if other.name != "ci.yml":
            assert "actions/cache" not in other.read_text(encoding="utf-8"), other.name


def test_the_lists_are_read_before_a_kept_package_is_judged_and_apt_installs():
    script = _install_script()
    update = script.index("sudo apt-get update")
    assert update < script.index("kept=0 dropped=0")
    install = script.index("sudo apt-get install")
    assert install > script.index('echo "Packages kept')
    assert 'Dir::Cache::Archives="$debs"' in script[install:]
    assert "--no-install-recommends" in script[install:]


def _stub(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("sha256sum") is None,
                    reason="needs bash and sha256sum")
def test_only_a_package_the_lists_name_with_its_sha256_is_kept(tmp_path):
    debs = tmp_path / "debs"
    debs.mkdir()
    good = debs / "good_1_amd64.deb"
    good.write_bytes(b"good=1\nthe package\n")
    changed = debs / "changed_2_amd64.deb"
    changed.write_bytes(b"changed=2\nnot what the lists name\n")
    other = debs / "other_3_amd64.deb"
    other.write_bytes(b"other=3\nnamed by no list\n")
    junk = debs / "junk_amd64.deb"
    junk.write_bytes(b"")
    good_sum = hashlib.sha256(good.read_bytes()).hexdigest()
    listed = {"good=1": good_sum, "changed=2": "0" * 64}

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # dpkg-deb --show --showformat=... FILE: the package's name=version, read
    # here from the file's first line; an empty file is no package at all.
    _stub(bin_dir, "dpkg-deb",
          'for a; do f="$a"; done\n'
          '[ -s "$f" ] || exit 2\n'
          'head -n 1 "$f" | tr -d "\\n"\n')
    cases = "".join(f'  {name}) printf "Package: x\\nSHA256: %s\\n" {sha} ;;\n'
                    for name, sha in listed.items())
    _stub(bin_dir, "apt-cache",
          f'[ "$1" = show ] || exit 1\ncase "$2" in\n{cases}  *) exit 100 ;;\nesac\n')
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    script = f'debs="{debs}"\n' + _check_loop() + 'echo "$kept $dropped"\n'
    out = subprocess.run(["bash", "-c", script], env=env, capture_output=True,
                         text=True, timeout=30, check=True)
    assert out.stdout.split() == ["1", "3"], out
    assert sorted(p.name for p in debs.iterdir()) == ["good_1_amd64.deb"]
