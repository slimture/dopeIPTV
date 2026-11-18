"""The Flatpak's manifest against what the app needs (#36): Qt's network
library links Kerberos' GSSAPI library, which the freedesktop runtime does
not carry, and every start crashed from 1.5.0 to 1.6.5. Read as text - a
test imports only what CI installs."""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "packaging", "flatpak", "io.github.slimture.dopeIPTV.yaml")


def _manifest() -> str:
    with open(MANIFEST, encoding="utf-8") as fh:
        return fh.read()


def _qt_modules_the_code_imports() -> set[str]:
    found: set[str] = set()
    for folder, _dirs, files in os.walk(os.path.join(ROOT, "dopeiptv")):
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(folder, name), encoding="utf-8") as fh:
                    found.update(re.findall(r"(?:from|import)\s+PyQt6\.(Qt\w+)", fh.read()))
    return found


def test_kerberos_is_built_before_the_app():
    text = _manifest()
    assert "  - name: krb5\n" in text
    assert text.index("  - name: krb5\n") < text.index("  - name: dopeiptv\n")
    block = text.split("  - name: krb5\n", 1)[1].split("\n  - name: ", 1)[0]
    assert re.search(r"url: https://kerberos\.org/dist/krb5/[\d.]+/krb5-[\d.]+\.tar\.gz", block)
    assert re.search(r"sha256: [0-9a-f]{64}\b", block)


def test_the_build_imports_every_qt_module_the_app_uses():
    # A library one of them needs and nothing in the bundle has stops the
    # build, never a start.
    line = next(ln for ln in _manifest().splitlines() if '"import PyQt6.' in ln)
    checked = set(re.findall(r"PyQt6\.(Qt\w+)", line))
    used = _qt_modules_the_code_imports()
    assert "QtNetwork" in used and "QtNetwork" in checked
    assert used <= checked, sorted(used - checked)


def test_the_app_starts_without_qts_network_library():
    # As in the Flatpak without Kerberos: the import fails, a second start
    # hands nothing over and opens its own window - nothing crashes.
    code = (
        "import sys\n"
        "sys.modules['PyQt6.QtNetwork'] = None\n"
        "from dopeiptv.core import instance\n"
        "assert instance.UNAVAILABLE, 'the missing library was not noticed'\n"
        "assert instance.send_to_running({'open': True}) is False\n"
        "server = instance.CommandServer(lambda cmd: None)\n"
        "assert server.listening is False\n"
        "server.close()\n"
        "print('STARTS_OK')\n"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          cwd=ROOT, env=env, timeout=60)
    assert "STARTS_OK" in proc.stdout, proc.stderr[-2000:]
