"""Optional custom-allocator preload for long-session memory.

Why: CPython's default allocator (glibc malloc on Linux, the system malloc on
macOS) fragments over a long-running session and is reluctant to hand freed
pages back to the OS - so a multi-hour video session's RSS creeps up and never
comes down. jemalloc / mimalloc reclaim far more aggressively; a 20-40% RSS cut
on long runs is common. Big long-lived Python/native apps (Redis, Rust
programs, browsers) ship a custom allocator for exactly this reason.

How: the allocator has to be in place before the interpreter's very first
allocation, so it can only be installed by *preloading* it into the process
(``LD_PRELOAD`` on Linux, ``DYLD_INSERT_LIBRARIES`` on macOS). That means
re-exec'ing ourselves once with the variable set. This runs as the very first
thing in the launcher, before Qt or anything heavy is imported.

Safety: this is a strict no-op unless a library is actually found, it guards
against re-exec loops, and every failure path falls through to a normal start
on the system allocator. It therefore cannot destabilise startup - worst case,
no effect. It is disabled by default (nothing is bundled yet); point
``DOPEIPTV_JEMALLOC`` at a library to try it, or set it to ``0`` to force it
off. Windows has no equivalent preload hook and is skipped.
"""
from __future__ import annotations

import os
import sys

_GUARD = "_DOPEIPTV_ALLOC"


def _candidate_libs() -> list[str]:
    """Ordered list of allocator libraries to try, most-specific first."""
    override = os.environ.get("DOPEIPTV_JEMALLOC")
    if override in ("0", "off", "no"):
        return []
    if override:
        return [override]
    # A library bundled next to the frozen app (added to the PyInstaller specs
    # only once the win is confirmed on the target platform). Names cover
    # jemalloc and mimalloc on each OS.
    roots = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        roots.append(base)
    roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    if sys.platform == "darwin":
        names = ("libjemalloc.2.dylib", "libjemalloc.dylib", "libmimalloc.dylib")
    else:
        names = ("libjemalloc.so.2", "libjemalloc.so", "libmimalloc.so")
    out = []
    for root in roots:
        for name in names:
            out.append(os.path.join(root, name))
    return out


def _preload_var() -> str | None:
    if sys.platform.startswith("linux"):
        return "LD_PRELOAD"
    if sys.platform == "darwin":
        return "DYLD_INSERT_LIBRARIES"
    return None   # Windows: no dynamic-preload hook


def maybe_preload_allocator(argv: list[str] | None = None) -> None:
    """Re-exec this process once with a custom allocator preloaded, if one is
    available for this platform. No-op (returns immediately) when already
    re-exec'd, when no library is found, or on any error."""
    if os.environ.get(_GUARD):
        return
    var = _preload_var()
    if var is None:
        return
    lib = next((p for p in _candidate_libs() if os.path.exists(p)), None)
    if not lib:
        return
    env = dict(os.environ)
    env[_GUARD] = "1"
    existing = env.get(var, "")
    env[var] = f"{lib}{os.pathsep}{existing}" if existing else lib
    if argv is None:
        # Frozen app: argv[0] is the exe (== sys.executable), so don't repeat
        # it. Script/`-m` run: argv[0] is the script, which must be passed on.
        argv = (sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv)
    try:
        os.execve(sys.executable, [sys.executable, *argv], env)
    except OSError:
        # Couldn't re-exec (e.g. exec disabled) - carry on with the system
        # allocator. The guard isn't set, but we simply proceed here.
        return
