#!/usr/bin/env python3
"""Known vulnerabilities in the Python packages dopeIPTV ships.

    python tools/audit_deps.py            # CI: fails on one that is not accepted

What is asked about - exactly the versions that go into the packages, on
every system, not what happens to be installed here:

- packaging/requirements-release.txt, the lock of the AppImage, .deb,
  Windows and macOS builds (every platform's entries: a Mac-only or
  Windows-only package is asked about on this Linux runner too);
- the wheels the Flatpak manifest names.

pip-audit does the asking (PyPI's advisory database). A finding fails the
run unless packaging/audit-allowlist.txt accepts it - with a reason, and a
date after which it fails again, so nothing is accepted for good. When the
advisory service cannot be reached nothing is known, and that is said as a
warning, not as a failure: CI must not go red because a server is down.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join(ROOT, "packaging", "requirements-release.txt")
FLATPAK = os.path.join(ROOT, "packaging", "flatpak", "io.github.slimture.dopeIPTV.yaml")
ALLOW = os.path.join(ROOT, "packaging", "audit-allowlist.txt")

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", re.M)
_WHEEL = re.compile(r"/([A-Za-z0-9][A-Za-z0-9._]*)-([0-9][A-Za-z0-9.!+_]*)-[^/\s]+\.whl\b")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_pins(text: str) -> set[tuple[str, str]]:
    """(name, version) of every pin in a pip lock, whatever its marker."""
    return {(_norm(n), v) for n, v in _PIN.findall(text)}


def flatpak_pins(text: str) -> set[tuple[str, str]]:
    """(name, version) of every wheel a Flatpak manifest fetches."""
    return {(_norm(n), v) for n, v in _WHEEL.findall(text)}


def allowlist(text: str) -> dict[tuple[str, str], tuple[datetime.date, str]]:
    """``<id> <package> <YYYY-MM-DD> <reason>`` per line -> {(id, package):
    (the day it stops being accepted, the reason)}. A line that is not so
    is an error: an allowlist nobody can read accepts nothing."""
    out: dict[tuple[str, str], tuple[datetime.date, str]] = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 3)
        if len(parts) < 4:
            raise ValueError(f"audit-allowlist.txt line {n}: want '<id> <package> <YYYY-MM-DD> <reason>'")
        out[(parts[0].upper(), _norm(parts[1]))] = (datetime.date.fromisoformat(parts[2]), parts[3])
    return out


def batches(pins: set[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """*pins* as lists with each package once: pip-audit takes one version
    of a package per file, and two builds may pin two (PyObjC)."""
    out: list[dict[str, str]] = []
    for name, version in sorted(pins):
        for b in out:
            if name not in b:
                b[name] = version
                break
        else:
            out.append({name: version})
    return [sorted(b.items()) for b in out]


def ask(pins: list[tuple[str, str]], runner=subprocess.run) -> list[dict] | None:
    """pip-audit's findings for *pins* as [{name, version, id, fix, aliases}];
    None when it could not ask."""
    with tempfile.TemporaryDirectory(prefix="audit-") as tmp:
        req, out = os.path.join(tmp, "requirements.txt"), os.path.join(tmp, "audit.json")
        with open(req, "w", encoding="utf-8") as fh:
            fh.writelines(f"{n}=={v}\n" for n, v in pins)
        try:
            proc = runner([sys.executable, "-m", "pip_audit", "--requirement", req, "--no-deps",
                           "--disable-pip", "--format", "json", "--output", out, "--progress-spinner", "off"],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                          timeout=900, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"::warning::pip-audit could not be run: {e}")
            return None
        try:
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            print(f"::warning::pip-audit gave no answer (exit {proc.returncode}): "
                  f"{(proc.stderr or '').strip()[-300:]}")
            return None
    found = []
    for dep in doc.get("dependencies", []) if isinstance(doc, dict) else []:
        for v in dep.get("vulns") or []:
            found.append({"name": _norm(str(dep.get("name"))), "version": str(dep.get("version")),
                          "id": str(v.get("id")), "aliases": [str(a) for a in v.get("aliases") or []],
                          "fix": ", ".join(v.get("fix_versions") or [])})
    return found


def judge(found: list[dict], allowed: dict[tuple[str, str], tuple[datetime.date, str]],
          today: datetime.date) -> tuple[list[str], list[str]]:
    """(what fails the run, what is accepted for now) as lines."""
    bad, ok = [], []
    seen: set[tuple[str, str, str]] = set()
    for f in found:
        ids = [f["id"].upper(), *(a.upper() for a in f["aliases"])]
        once = (f["name"], f["version"], min(ids))      # one advisory, said under two of its names
        if once in seen:
            continue
        seen.add(once)
        what = f"{f['name']} {f['version']}: {f['id']}" + (f" (fixed in {f['fix']})" if f["fix"] else "")
        entry = next((allowed[(i, f["name"])] for i in ids if (i, f["name"]) in allowed), None)
        if entry is None:
            bad.append(what)
        elif entry[0] < today:
            bad.append(f"{what} - accepted until {entry[0]}, which has passed: {entry[1]}")
        else:
            ok.append(f"{what} - accepted until {entry[0]}: {entry[1]}")
    return bad, ok


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--today", default="", help="YYYY-MM-DD (tests)")
    a = p.parse_args(argv)
    today = datetime.date.fromisoformat(a.today) if a.today else datetime.date.today()
    with open(LOCK, encoding="utf-8") as fh:
        groups = {"the packaged builds (requirements-release.txt)": lock_pins(fh.read())}
    with open(FLATPAK, encoding="utf-8") as fh:
        groups["the Flatpak's wheels"] = flatpak_pins(fh.read())
    with open(ALLOW, encoding="utf-8") as fh:
        allowed = allowlist(fh.read())
    failed, unknown = [], False
    for title, pins in groups.items():
        print(f"== {title}: {len(pins)} pinned packages")
        found: list[dict] = []
        for batch in batches(pins):
            got = ask(batch)
            if got is None:
                unknown = True
                continue
            found += got
        bad, ok = judge(found, allowed, today)
        for line in ok:
            print(f"   accepted: {line}")
        for line in bad:
            print(f"::error::{title}: {line}")
        failed += bad
        if not bad and not ok and not unknown:
            print("   no known vulnerability")
    if failed:
        print(f"\n{len(failed)} known vulnerabilities in what is shipped. Move the lock to a fixed version "
              "(docs/MAINTAINING.md, 'What the build downloads'), or accept one for a while, with the "
              "reason, in packaging/audit-allowlist.txt.")
        return 1
    if unknown:
        print("::warning::the advisory service could not be asked this time: nothing is known, nothing failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
