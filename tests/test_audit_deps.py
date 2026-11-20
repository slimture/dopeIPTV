"""The audit of what is shipped fails closed (tools/audit_deps.py): an audit
that did not finish - no answer, a package left unchecked - fails, and the
release's signing waits for it to pass (release.yml)."""
from __future__ import annotations

import importlib.util
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tool():
    spec = importlib.util.spec_from_file_location("audit_deps", os.path.join(ROOT, "tools", "audit_deps.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _runner(doc):
    def run(args, **_k):
        out = args[args.index("--output") + 1]
        if doc is not None:
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)

        class P:
            returncode = 0 if doc is not None else 1
            stderr = "" if doc is not None else "Connection refused"
        return P()
    return run


def test_an_answer_that_leaves_a_package_unchecked_is_no_answer(capsys):
    a = _tool()
    pins = [("requests", "2.32.5"), ("urllib3", "2.5.0")]
    full = {"dependencies": [{"name": "requests", "version": "2.32.5", "vulns": []},
                             {"name": "urllib3", "version": "2.5.0",
                              "vulns": [{"id": "GHSA-x", "aliases": ["CVE-1"], "fix_versions": ["2.6.0"]}]}]}
    got = a.ask(pins, _runner(full))
    assert got is not None and [f["id"] for f in got] == ["GHSA-x"]
    skipped = {"dependencies": [{"name": "requests", "version": "2.32.5", "vulns": []},
                                {"name": "urllib3", "version": "2.5.0", "skip_reason": "not on PyPI"}]}
    assert a.ask(pins, _runner(skipped)) is None
    missing = {"dependencies": [{"name": "requests", "version": "2.32.5", "vulns": []}]}
    assert a.ask(pins, _runner(missing)) is None
    assert a.ask(pins, _runner(None)) is None
    out = capsys.readouterr().out
    assert "did not check urllib3" in out and "left unchecked: urllib3" in out


def test_no_answer_is_asked_again_then_fails_the_run():
    a = _tool()
    slept = []
    calls = []

    def run(args, **k):
        calls.append(1)
        return _runner(None)(args, **k)
    assert a.ask_until([("requests", "2.32.5")], run, slept.append) is None
    assert len(calls) == a.TRIES == 3 and slept == [a.WAIT] * (a.TRIES - 1)
    assert a.main(["--today", "2026-11-20"], asker=lambda batch: None) == 2, "not finished: failed"
    assert a.main(["--today", "2026-11-20"], asker=lambda batch: []) == 0
    finding = [{"name": "requests", "version": "1", "id": "GHSA-new", "aliases": [], "fix": ""}]
    assert a.main(["--today", "2026-11-20"], asker=lambda batch: finding) == 1


def test_the_release_is_signed_only_once_the_audit_passed():
    with open(os.path.join(ROOT, ".github", "workflows", "release.yml"), encoding="utf-8") as fh:
        wf = fh.read()
    audit = wf[wf.index("\n  audit:\n"):]
    audit = audit[:audit.index("\n  checksums:\n")]
    assert "run: python tools/audit_deps.py" in audit
    assert "--require-hashes -r packaging/requirements-audit.txt" in audit
    head = wf[wf.index("\n  checksums:\n"):][:600]
    needs = re.search(r"needs: \[([^\]]+)\]", head).group(1)
    assert "audit" in [n.strip() for n in needs.split(",")]
    assert "needs.audit.result == 'success'" in head, "!cancelled() alone would sign after a failed audit"
    with open(os.path.join(ROOT, "tools", "audit_deps.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "nothing is known, nothing failed" not in src
