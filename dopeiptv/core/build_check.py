"""Is this copy of dopeIPTV the one the release workflow built?

Every package release.yml builds carries a list of the app's own files -
each one's SHA-256, and the version - signed with dopeIPTV's release key,
the one that signs each release's checksums (tools/build_manifest.py,
core/update_keys.py). It lies beside the package inside the bundle
(``dopeiptv/_build_manifest.json`` and its ``.sig``) and never in git.

- "official": a frozen build (PyInstaller), or a package installed with a
  list (the Flatpak), whose list's signature is by that key, made for this
  version, every file it names as it names it, and no code beside them.
- "modified": a frozen build without such a list, or with one that does not
  check out - with the files that differ, are extra or are missing.
- "source": a checkout or a pip install without a list. Everything works
  there, as from a build: it is how the app is made.

What a frozen build is checked by: its data files on disk, and its modules
as PyInstaller stored them (the entries of its archive, which is what runs
- the .py files are not in the bundle). The list is made of the bundle by
the bundle itself (``--build-files``), so both sides read the same.

Online services that dopeiptv.org runs (watch together, settings sync,
reports, updates) are off in a modified build, and every request to the
site says which kind this is (HEADER). This stops the ones who do not
bother; a fork that takes the check out cannot be stopped by it.

Worked out once, on a thread of its own at the start (:func:`start`), and
kept; :func:`verdict` waits for it the first time. No Qt.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from . import ed25519
from .log import log
from .update_keys import UPDATE_KEYS

MANIFEST = "_build_manifest.json"
SIGNATURE = MANIFEST + ".sig"
FORMAT = 1
#: Sent with every request to dopeiptv.org: official, modified or source.
HEADER = "X-dopeIPTV-Build"
OFFICIAL, MODIFIED, SOURCE = "official", "modified", "source"
#: How the list was made: of a frozen bundle, or of a package as pip has it.
FROZEN, TREE = "frozen", "tree"
#: A file of these kinds that the list does not name is code nobody signed.
CODE = (".py", ".pyc", ".pyo", ".pyw", ".pyd", ".so", ".dylib", ".dll")
#: How many names a verdict keeps of each kind.
KEEP = 50
#: The services that are off in a modified build (their i18n names).
SERVICES = {"together": "build_svc_together", "sync": "build_svc_sync",
            "updates": "build_svc_updates"}
_OWN = (MANIFEST, SIGNATURE)
_SKIP_DIRS = ("__pycache__",)
#: PyInstaller's archive entry kinds (pyimod01_archive).
_PYZ_PKG, _PYZ_NSPKG = 1, 3


@dataclass(frozen=True)
class Verdict:
    """What this copy is. *reason* (modified): "unsigned" (no list),
    "signature" (not by the release key, or unreadable), "version" (made
    for another version or kind of package), "files" (they differ),
    "unreadable" (the bundle could not be read)."""

    kind: str
    reason: str = ""
    changed: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    count: int = 0
    git: str = ""

    @property
    def names(self) -> tuple[str, ...]:
        """The files that differ, extra ones first, as the package names
        them ("custom_skin.py", "ui/theme.py")."""
        return tuple(_short(n) for n in (*self.extra, *self.changed, *self.missing))

    def summary(self, most: int = 3) -> str:
        """``official``, ``modified (custom_skin.py, … 4 files)`` or ``from
        source (git 3078763)`` - in English, for a report and the log."""
        if self.kind == OFFICIAL:
            return "official"
        if self.kind == SOURCE:
            return "from source" + (f" (git {self.git})" if self.git else "")
        if self.count:
            shown = list(self.names[:max(1, most)])
            if self.count > len(shown):
                shown.append(f"… {self.count} files")
            return f"modified ({', '.join(shown)})"
        why = {"unsigned": "no signed file list", "signature": "file list not signed by dopeIPTV",
               "version": "file list of another build", "unreadable": "bundle not readable"}
        return f"modified ({why.get(self.reason, self.reason or 'unknown')})"

    def line(self) -> str:
        """The report's header line: ``Build: official``."""
        return f"Build: {self.summary()}"


def _short(name: str) -> str:
    return name.split("/", 1)[1] if name.startswith("dopeiptv/") else name


# -- what is there -----------------------------------------------------------------

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def disk_files(pkg_dir: str, prefix: str = "dopeiptv") -> dict[str, str]:
    """Every file in the package's folder - but __pycache__ and the list
    itself - as ``"dopeiptv/<path>"`` -> SHA-256 ("" when unreadable).
    Linked folders are followed (a macOS bundle links them), each once."""
    root = os.path.realpath(pkg_dir)
    out: dict[str, str] = {}
    seen: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen:
            dirnames[:] = []
            continue
        seen.add(real)
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            if dirpath == root and name in _OWN:
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                out[f"{prefix}/{rel}"] = _sha256_file(path)
            except OSError:
                out[f"{prefix}/{rel}"] = ""
    return out


def archive_files(archive: Any, package: str = "dopeiptv") -> dict[str, str]:
    """The package's modules in a PyInstaller archive, each under the .py
    path it was made from -> the SHA-256 of its entry as stored."""
    toc = getattr(archive, "toc", None)
    if not isinstance(toc, Mapping):
        raise LookupError("the bundle's archive has no table of contents")
    out: dict[str, str] = {}
    for name, entry in toc.items():
        if not isinstance(name, str) or (name != package and not name.startswith(package + ".")):
            continue
        typecode = entry[0]
        if typecode == _PYZ_NSPKG:
            continue
        data = archive.extract(name, raw=True)
        if not isinstance(data, (bytes, bytearray)):
            raise LookupError(f"the archive gave no data for {name}")
        path = name.replace(".", "/") + ("/__init__.py" if typecode == _PYZ_PKG else ".py")
        out[path] = hashlib.sha256(bytes(data)).hexdigest()
    if not out:
        raise LookupError("the package is not in the bundle's archive")
    return out


def frozen_archive() -> Any:
    """The archive this frozen app imports its modules from (PyInstaller's
    reader), or None."""
    mod = sys.modules.get("pyimod02_importers")
    arc = getattr(mod, "pyz_archive", None)
    if arc is None:
        pkg = sys.modules.get(__name__.rsplit(".", 2)[0])
        arc = getattr(getattr(pkg, "__loader__", None), "_pyz_archive", None)
    return arc


def collect(pkg_dir: str, frozen: bool, archive: Any = None) -> tuple[dict[str, str], list[str]]:
    """(what is there, names both on disk and in the archive). A frozen
    build: its archive's modules and the files on disk; otherwise the files
    on disk."""
    disk = disk_files(pkg_dir)
    if not frozen:
        return disk, []
    found = archive_files(archive)
    clashes = sorted(k for k in disk if k in found)
    for k, v in disk.items():
        found.setdefault(k, v)
    return found, clashes


def compare(listed: Mapping[str, str], found: Mapping[str, str]) -> tuple[list[str], list[str], list[str]]:
    """(changed, extra code, missing). A data file the list does not name
    is no code and is left alone."""
    changed = sorted(k for k, v in listed.items() if k in found and found[k] != v)
    missing = sorted(k for k in listed if k not in found)
    extra = sorted(k for k in found if k not in listed and k.lower().endswith(CODE))
    return changed, extra, missing


# -- the list ----------------------------------------------------------------------

def make(files: Mapping[str, str], version: str, mode: str) -> bytes:
    """The list, as the bytes that are signed and kept."""
    doc = {"app": "dopeIPTV", "format": FORMAT, "version": version, "mode": mode,
           "files": dict(sorted(files.items()))}
    return (json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def sign(seed: bytes, data: bytes) -> str:
    """The signature file's text: base64 of the Ed25519 signature, one line."""
    return base64.b64encode(ed25519.sign(seed, data)).decode("ascii") + "\n"


def signed_by(data: bytes, sig_text: str, keys: Iterable[str]) -> bool:
    """Whether *sig_text* signs *data* with one of *keys* (base64)."""
    try:
        sig = base64.b64decode(sig_text.strip(), validate=True)
    except ValueError:
        return False
    for k in keys:
        try:
            pub = base64.b64decode(k, validate=True)
        except ValueError:
            continue
        if ed25519.verify(pub, data, sig):
            return True
    return False


def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def in_checkout(root: str) -> bool:
    """A git checkout: the package's parent holds .git."""
    return os.path.exists(os.path.join(root, ".git"))


def git_head(root: str) -> str:
    """The checkout's commit, short ("3078763"); "" when not one. Read from
    .git itself (a worktree's too), never by running git."""
    try:
        dot = os.path.join(root, ".git")
        if os.path.isfile(dot):
            with open(dot, encoding="utf-8") as fh:
                first = fh.read().strip()
            if not first.startswith("gitdir:"):
                return ""
            gitdir = os.path.normpath(os.path.join(root, first[7:].strip()))
        elif os.path.isdir(dot):
            gitdir = dot
        else:
            return ""
        with open(os.path.join(gitdir, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
        sha = head
        if head.startswith("ref:"):
            ref = head[4:].strip()
            common = gitdir
            cpath = os.path.join(gitdir, "commondir")
            if os.path.isfile(cpath):
                with open(cpath, encoding="utf-8") as fh:
                    common = os.path.normpath(os.path.join(gitdir, fh.read().strip()))
            sha = ""
            for base in (gitdir, common):
                p = os.path.join(base, *ref.split("/"))
                if os.path.isfile(p):
                    with open(p, encoding="utf-8") as fh:
                        sha = fh.read().strip()
                    break
            else:
                packed = os.path.join(common, "packed-refs")
                if os.path.isfile(packed):
                    with open(packed, encoding="utf-8") as fh:
                        for line in fh:
                            parts = line.split()
                            if len(parts) == 2 and parts[1] == ref:
                                sha = parts[0]
                                break
        return sha[:7] if re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", sha) else ""
    except (OSError, UnicodeDecodeError):
        return ""


def check(pkg_dir: str, frozen: bool, version: str, archive: Any = None,
          keys: Iterable[str] = UPDATE_KEYS, root: str | None = None) -> Verdict:
    """What the package in *pkg_dir* is (see the module's words)."""
    root = os.path.dirname(os.path.realpath(pkg_dir)) if root is None else root
    data = _read(os.path.join(pkg_dir, MANIFEST))
    if not frozen and (data is None or in_checkout(root)):
        return Verdict(SOURCE, git=git_head(root))
    if data is None:
        return Verdict(MODIFIED, reason="unsigned")
    sig = _read(os.path.join(pkg_dir, SIGNATURE))
    if sig is None or not signed_by(data, sig.decode("ascii", "replace"), keys):
        return Verdict(MODIFIED, reason="signature")
    try:
        doc = json.loads(data.decode("ascii"))
        listed = doc["files"]
        if not isinstance(listed, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in listed.items()):
            raise TypeError("files")
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return Verdict(MODIFIED, reason="signature")
    if doc.get("format") != FORMAT or doc.get("version") != version \
            or doc.get("mode") != (FROZEN if frozen else TREE):
        return Verdict(MODIFIED, reason="version")
    try:
        found, clashes = collect(pkg_dir, frozen, archive)
    except Exception as e:
        log.warning("build: the bundle could not be read (%s)", e)
        return Verdict(MODIFIED, reason="unreadable")
    changed, extra, missing = compare(listed, found)
    extra = sorted({*extra, *clashes})
    count = len(changed) + len(extra) + len(missing)
    if count:
        return Verdict(MODIFIED, reason="files", changed=tuple(changed[:KEEP]),
                       extra=tuple(extra[:KEEP]), missing=tuple(missing[:KEEP]), count=count)
    return Verdict(OFFICIAL)


# -- this copy ---------------------------------------------------------------------

def package_dir() -> str:
    """The folder of the running dopeiptv package (in a frozen build, the
    one inside the bundle)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _compute() -> Verdict:
    from .. import VERSION
    frozen = is_frozen()
    try:
        return check(package_dir(), frozen, VERSION,
                     archive=frozen_archive() if frozen else None)
    except Exception as e:
        log.warning("build: not checked (%s)", e)
        return Verdict(MODIFIED, reason="unreadable") if frozen else Verdict(SOURCE)


_lock = threading.Lock()
_verdict: Verdict | None = None
_thread: threading.Thread | None = None


def _settle(v: Verdict) -> Verdict:
    global _verdict
    with _lock:
        if _verdict is None:
            _verdict = v
            log.info("build: %s", v.summary())
        return _verdict


def _run() -> None:
    _settle(_compute())


def start() -> None:
    """Check on a thread of its own (the app's start): the window never
    waits for it."""
    global _thread
    with _lock:
        if _verdict is not None or _thread is not None:
            return
        _thread = threading.Thread(target=_run, name="build-check", daemon=True)
        _thread.start()


def verdict(timeout: float = 10.0) -> Verdict:
    """This copy's verdict - the start's, waited for the first time; worked
    out here when nothing started it."""
    v = _verdict
    if v is not None:
        return v
    t = _thread
    if t is not None:
        t.join(timeout)
        if _verdict is not None:
            return _verdict
    return _settle(_compute())


def settled() -> Verdict | None:
    """The verdict if it is known already - never waits (the window's
    first moments)."""
    return _verdict


def set_verdict(v: Verdict | None) -> None:
    """Tests: what this copy is said to be (None: worked out again)."""
    global _verdict, _thread
    with _lock:
        _verdict, _thread = v, None


def kind() -> str:
    return verdict().kind


def allowed() -> bool:
    """dopeiptv.org's services: on, unless this is a modified build."""
    return kind() != MODIFIED


def headers() -> dict[str, str]:
    """What every request to dopeiptv.org carries."""
    return {HEADER: kind()}


#: Where dopeiptv.org's services are - and the only place secure() lets a
#: report, a synced copy or a room's message go.
SERVICE_HOST = "dopeiptv.org"
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def secure(url: str) -> bool:
    """Whether a request to one of dopeiptv.org's services may go to *url*:
    https to dopeiptv.org itself - or this computer, for the tests' own
    servers. Anything else is refused before a connection is made (a
    changed endpoint, a typo, another host)."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(str(url))
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if host in _LOOPBACK:
        return parts.scheme in ("http", "https")
    return parts.scheme == "https" and host == SERVICE_HOST


class NotOfficial(RuntimeError):
    """A service of dopeiptv.org asked for in a modified build."""

    def __init__(self, service: str) -> None:
        self.service = service
        super().__init__(f"this is not an official build of dopeIPTV: {service} is off")

    def localized(self) -> str:
        return notice(self.service)


def refuse(service: str) -> None:
    """Raise NotOfficial in a modified build."""
    if not allowed():
        raise NotOfficial(service)


def notice(service: str) -> str:
    """The one friendly line a place says where *service* is off."""
    from ..i18n import tr
    return tr("build_not_official", service=tr(SERVICES.get(service, service)))


# -- for the build (tools/build_manifest.py runs this through the bundle) -----------

def files_main(argv: list[str]) -> int:
    """``--build-files OUT``: this copy's files and verdict, as JSON in OUT
    - how a bundle tells the build what it holds."""
    if len(argv) != 1:
        return 2
    from .. import VERSION
    frozen = is_frozen()
    pkg = package_dir()
    out: dict[str, Any] = {"frozen": frozen, "package": pkg, "version": VERSION,
                           "mode": FROZEN if frozen else TREE}
    archive = frozen_archive() if frozen else None
    try:
        found, _clashes = collect(pkg, frozen, archive)
        out["files"] = found
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    v = check(pkg, frozen, VERSION, archive=archive)
    out["verdict"] = {"kind": v.kind, "summary": v.summary(), "names": list(v.names)}
    with open(argv[0], "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return 0 if "files" in out else 1
