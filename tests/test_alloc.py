"""The optional allocator preload must be a strict no-op by default.

It re-exec's the process to preload jemalloc/mimalloc, so a bug here could
break every startup. These tests lock in that (a) with nothing bundled and no
override it does nothing, and (b) it never re-exec's once the guard is set.
"""
from dopeiptv.core import _alloc


def test_no_library_is_a_no_op(monkeypatch):
    # No override, nothing bundled next to the interpreter -> returns without
    # touching os.execve.
    monkeypatch.delenv("DOPEIPTV_JEMALLOC", raising=False)
    monkeypatch.delenv(_alloc._GUARD, raising=False)
    called = []
    monkeypatch.setattr(_alloc.os, "execve",
                        lambda *a: called.append(a))
    monkeypatch.setattr(_alloc, "_candidate_libs", lambda: [])
    _alloc.maybe_preload_allocator()
    assert called == []


def test_disabled_override_is_a_no_op(monkeypatch):
    monkeypatch.setenv("DOPEIPTV_JEMALLOC", "0")
    monkeypatch.delenv(_alloc._GUARD, raising=False)
    called = []
    monkeypatch.setattr(_alloc.os, "execve", lambda *a: called.append(a))
    _alloc.maybe_preload_allocator()
    assert called == []


def test_guard_prevents_reexec_loop(monkeypatch):
    # Already preloaded (guard set) -> never re-exec again.
    monkeypatch.setenv(_alloc._GUARD, "1")
    monkeypatch.setenv("DOPEIPTV_JEMALLOC", "/nonexistent/libjemalloc.so")
    called = []
    monkeypatch.setattr(_alloc.os, "execve", lambda *a: called.append(a))
    _alloc.maybe_preload_allocator()
    assert called == []


def test_reexec_only_when_the_library_exists(monkeypatch, tmp_path):
    lib = tmp_path / "libjemalloc.so.2"
    lib.write_bytes(b"")   # exists
    monkeypatch.setenv("DOPEIPTV_JEMALLOC", str(lib))
    monkeypatch.delenv(_alloc._GUARD, raising=False)
    monkeypatch.setattr(_alloc.sys, "platform", "linux")
    captured = {}

    def fake_execve(exe, argv, env):
        captured["var"] = env.get("LD_PRELOAD")
        captured["guard"] = env.get(_alloc._GUARD)
        raise OSError("blocked in test")   # stop it actually exec'ing

    monkeypatch.setattr(_alloc.os, "execve", fake_execve)
    _alloc.maybe_preload_allocator(argv=["x"])
    assert str(lib) in (captured.get("var") or "")
    assert captured.get("guard") == "1"
