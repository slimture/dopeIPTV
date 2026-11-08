"""Make the macOS app's libmpv and ffmpeg self-contained (the Intel build).

conda-forge's libraries name each other as @rpath/<name> and find each
other through an @loader_path rpath. In the frozen app some of them were
missing, or still pointed into the build machine's conda environment, so
libmpv could not load on a Mac without it. This walks libmpv.2.dylib and
ffmpeg in Contents/Frameworks, copies every library they need - and those
need - from the environment's lib/ into Frameworks, points each reference
at @rpath/<name>, and gives every file an @loader_path rpath. System
libraries (/usr/lib, /System) are left as they are.

    python packaging/macos_bundle_dylibs.py dist/dopeIPTV.app <env>/lib

Only the release workflow's Intel leg runs it; it needs otool and
install_name_tool (Xcode's command line tools).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOTS = ("libmpv.2.dylib", "ffmpeg")


def _run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True,
                          check=True).stdout


def install_id(path: str) -> str | None:
    lines = _run("otool", "-D", path).splitlines()[1:]
    return lines[0].strip() if lines else None


def dependencies(path: str) -> list[str]:
    own = install_id(path)
    out = []
    for line in _run("otool", "-L", path).splitlines()[1:]:
        name = line.strip().split(" (compatibility")[0].strip()
        if name and name != own:
            out.append(name)
    return out


def rpaths(path: str) -> list[str]:
    out, want = [], False
    for line in _run("otool", "-l", path).splitlines():
        line = line.strip()
        if line.startswith("cmd "):
            want = line == "cmd LC_RPATH"
        elif want and line.startswith("path "):
            out.append(line[5:].split(" (offset")[0])
            want = False
    return out


def is_system(name: str) -> bool:
    return name.startswith(("/usr/lib/", "/System/"))


def bundle(app: str, envlib: str) -> int:
    fw = os.path.join(app, "Contents", "Frameworks")
    env_root = os.path.dirname(os.path.realpath(envlib))
    todo = [os.path.realpath(os.path.join(fw, n)) for n in ROOTS
            if os.path.exists(os.path.join(fw, n))]
    if not todo:
        print(f"nothing to do: no {' or '.join(ROOTS)} in {fw}")
        return 1
    seen: set[str] = set()
    copied = missing = 0
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for dep in dependencies(f):
            if is_system(dep):
                continue
            base = os.path.basename(dep)
            ours = os.path.join(fw, base)
            if not os.path.exists(ours):
                src = os.path.join(envlib, base)
                if not os.path.exists(src):
                    print(f"MISSING: {base} (needed by {os.path.basename(f)})")
                    missing += 1
                    continue
                shutil.copy2(os.path.realpath(src), ours)
                os.chmod(ours, 0o755)
                _run("install_name_tool", "-id", f"@rpath/{base}", ours)
                copied += 1
                print(f"bundled {base}")
            todo.append(os.path.realpath(ours))
            if dep != f"@rpath/{base}":
                _run("install_name_tool", "-change", dep, f"@rpath/{base}", f)
        have = rpaths(f)
        for rp in have:
            if rp.startswith(env_root) or rp.startswith("/Users/"):
                _run("install_name_tool", "-delete_rpath", rp, f)
        if "@loader_path" not in have and "@loader_path/" not in have:
            _run("install_name_tool", "-add_rpath", "@loader_path", f)
    print(f"{len(seen)} file(s) checked, {copied} library(ies) added, "
          f"{missing} missing")
    return 1 if missing else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(bundle(sys.argv[1], sys.argv[2]))
