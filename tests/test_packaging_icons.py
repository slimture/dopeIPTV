"""The Flatpak's icons are committed binaries, and a committed binary is a
copy nobody regenerates.

Its icon sat at 256 px and full-bleed while the app, the .deb and the
AppImage all moved to five sizes with a margin - and nothing anywhere
said so, because a PNG in the tree looks the same however wrong it is.
So: every committed icon is compared against what the drawing code
produces today, and a mismatch says which file to regenerate.
"""
import os
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os, pathlib, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QBuffer
from PyQt6.QtWidgets import QApplication

from dopeiptv.app import ICON_SAFE_INSET, make_app_icon

app = QApplication([])
icon = make_app_icon(inset=ICON_SAFE_INSET)
here = pathlib.Path("packaging/icons")

stale = []
for size in (256, 128, 64, 48, 32):
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    assert icon.pixmap(size, size).save(buf, "PNG")
    buf.close()
    p = here / ("io.github.slimture.dopeIPTV-%d.png" % size)
    if not p.exists():
        stale.append("%s is missing" % p)
    elif p.read_bytes() != bytes(buf.data()):
        stale.append("%s differs from what make_app_icon draws" % p)

if stale:
    print("STALE: " + "; ".join(stale))
else:
    print("ICONS_OK")
"""


def test_the_committed_flatpak_icons_match_the_drawing_code():
    """Regenerate with:

        QT_QPA_PLATFORM=offscreen python - <<'EOF'
        from PyQt6.QtWidgets import QApplication
        from dopeiptv.app import ICON_SAFE_INSET, make_app_icon
        app = QApplication([])
        icon = make_app_icon(inset=ICON_SAFE_INSET)
        for s in (256, 128, 64, 48, 32):
            icon.pixmap(s, s).save(
                "packaging/icons/io.github.slimture.dopeIPTV-%d.png" % s, "PNG")
        EOF
    """
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD], capture_output=True, text=True,
        cwd=_REPO, timeout=180,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert "ICONS_OK" in proc.stdout, (
        proc.stdout + "\n" + proc.stderr[-2000:])


def test_the_flatpak_manifest_installs_every_size():
    """One 256 px file meant a 32 px panel downscaled it, which is the
    softness the .deb had until it shipped all five."""
    import yaml

    with open(os.path.join(
            _REPO, "packaging/flatpak/io.github.slimture.dopeIPTV.yaml")) as f:
        doc = yaml.safe_load(f)
    cmds = [c for m in doc["modules"] if isinstance(m, dict)
            for c in m.get("build-commands", [])]
    icon_cmds = [c for c in cmds if "icons/hicolor" in c]
    assert icon_cmds, "the manifest installs no icon at all"
    joined = " ".join(icon_cmds)
    for size in (256, 128, 64, 48, 32):
        assert str(size) in joined, "size %d is not installed" % size


def test_every_repo_file_a_workflow_names_actually_exists():
    """A workflow step referencing a file that is not in the tree fails the
    release build, and nothing local catches it: plain grep and ripgrep both
    SKIP hidden directories, so a reference from `.github/` is invisible to
    the obvious "is this still used?" check.

    That is not hypothetical. packaging/io.github.slimture.dopeIPTV.png was
    deleted when the Flatpak icons moved, the grep for references came back
    clean because it never looked in .github/, and both Windows jobs went
    red on "Build .ico from the app PNG".
    """
    import glob
    import re

    # Repo-relative paths, quoted or bare, under the directories that hold
    # committed assets. Deliberately narrow: this is about FILES IN THE
    # TREE, not about ${{ }} expressions.
    pattern = re.compile(
        r"(?<![\w/.-])((?:packaging|docs|dopeiptv)/[\w./-]+\.[a-z0-9]{2,5})")

    # Paths the workflow WRITES rather than reads. Listed one by one on
    # purpose: a new entry here should be a deliberate decision, not a
    # pattern quietly widening until it stops catching anything.
    generated = {
        "dopeiptv/_tmdb_key.py",        # "Bake built-in TMDB key" writes it
        "packaging/dopeIPTV.ico",       # "Build .ico from the app PNG" does
    }
    missing = []
    for wf in glob.glob(os.path.join(_REPO, ".github/workflows/*.yml")):
        with open(wf, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue        # a comment naming a path is not a use
                for hit in pattern.findall(line):
                    if "*" in hit or "${{" in hit or hit in generated:
                        continue
                    if not os.path.exists(os.path.join(_REPO, hit)):
                        missing.append(
                            "%s:%d references %s, which does not exist"
                            % (os.path.basename(wf), lineno, hit))
    assert not missing, "\n".join(missing)
