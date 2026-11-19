"""The Windows bundle is built on a runner that cannot show what a user's
PC lacks, so two things are checked by reading files rather than running
them: which libmpv archive the release picks (a substring test also
matched the x86-64-v3/AVX2 build, and the listing order chose), and
whether the DLLs we ship import anything Windows itself does not provide
(issue #19: libmpv hard-links vulkan-1.dll, a driver-installed file that
1.2.11 did not bundle, so PCs without a Vulkan driver had no in-app video).

The audit reads PE import tables itself, so it is exercised here on a
hand-built PE: a real libmpv is 120 MB and Windows-only.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import struct
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _tool(name: str):
    spec = importlib.util.spec_from_file_location(
        name, _REPO / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


pick_mpv_asset = _tool("pick_mpv_asset")
win_dll_audit = _tool("win_dll_audit")


# ------------------------------------------------------------ the picker ---

_ASSETS = [
    "mpv-dev-x86_64-v3-20260829-git-e8673660ab.7z",
    "mpv-dev-x86_64-20260829-git-e8673660ab.7z",
    "mpv-dev-lgpl-x86_64-20260829-git-e8673660ab.7z",
    "mpv-dev-aarch64-20260829-git-e8673660ab.7z",
    "mpv-x86_64-20260829-git-e8673660ab.7z",
    "mpv-x86_64-v3-20260829-git-e8673660ab.7z",
]


def test_the_baseline_archive_is_picked_whatever_the_listing_order():
    want = "mpv-dev-x86_64-20260829-git-e8673660ab.7z"
    assert pick_mpv_asset.pick(_ASSETS, "x86_64") == want
    assert pick_mpv_asset.pick(list(reversed(_ASSETS)), "x86_64") == want
    assert (pick_mpv_asset.pick(_ASSETS, "aarch64")
            == "mpv-dev-aarch64-20260829-git-e8673660ab.7z")


def test_the_picker_refuses_to_guess():
    with pytest.raises(LookupError):
        pick_mpv_asset.pick([n for n in _ASSETS if "-v3-" in n], "x86_64")
    with pytest.raises(LookupError):
        pick_mpv_asset.pick(_ASSETS + [
            "mpv-dev-x86_64-20260830-git-0123abcd.7z"], "x86_64")


def test_the_picker_cli_prints_the_url_and_fails_loud_on_a_rate_limit(
        monkeypatch, capsys):
    release = {"assets": [
        {"name": n, "browser_download_url": f"https://x/{n}"} for n in _ASSETS]}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(release)))
    assert pick_mpv_asset.main(["x86_64"]) == 0
    assert capsys.readouterr().out.strip() == (
        "https://x/mpv-dev-x86_64-20260829-git-e8673660ab.7z")

    monkeypatch.setattr(sys, "stdin", io.StringIO(
        '{"message": "API rate limit exceeded"}'))
    assert pick_mpv_asset.main(["x86_64"]) == 1
    assert "::error::" in capsys.readouterr().err


# ------------------------------------------------------------- the audit ---

def _pe(imports: list[str], pe32plus: bool = True) -> bytes:
    """A minimal PE whose import directory names *imports*: DOS stub, PE
    signature, COFF header, optional header (data directories only), one
    section holding the import descriptors and the name strings."""
    sect_va, sect_raw = 0x1000, 0x400
    names_off = 20 * (len(imports) + 1)
    descriptors, strings = bytearray(), bytearray()
    for name in imports:
        name_rva = sect_va + names_off + len(strings)
        strings += name.encode() + b"\0"
        descriptors += struct.pack("<IIIII", 0, 0, 0, name_rva, 0)
    descriptors += b"\0" * 20
    section = bytes(descriptors + strings)

    opt_size, dirs_off = (240, 112) if pe32plus else (224, 96)
    opt = bytearray(opt_size)
    struct.pack_into("<H", opt, 0, 0x20B if pe32plus else 0x10B)
    struct.pack_into("<II", opt, dirs_off + 8, sect_va, len(descriptors))
    coff = struct.pack("<HHIIIHH", 0x8664 if pe32plus else 0x14C, 1, 0, 0, 0,
                       opt_size, 0x2022)
    header = struct.pack("<8sIIIIIIHHI", b".rdata", len(section), sect_va,
                         len(section), sect_raw, 0, 0, 0, 0, 0x40000040)

    head = bytearray(sect_raw)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x40)
    head[0x40:0x44] = b"PE\0\0"
    head[0x44:0x58] = coff
    head[0x58:0x58 + opt_size] = opt
    head[0x58 + opt_size:0x58 + opt_size + 40] = header
    return bytes(head) + section


_IMPORTS = ["KERNEL32.dll", "vulkan-1.dll", "api-ms-win-crt-runtime-l1-1-0.dll"]


@pytest.mark.parametrize("pe32plus", [True, False])
def test_the_import_table_is_read_in_order(tmp_path, pe32plus):
    dll = tmp_path / "mpv-2.dll"
    dll.write_bytes(_pe(_IMPORTS, pe32plus))
    assert win_dll_audit.imports_of(str(dll)) == _IMPORTS


def test_the_vulkan_loader_counts_only_when_shipped_beside_the_dll(tmp_path):
    dll = tmp_path / "mpv-2.dll"
    dll.write_bytes(_pe(_IMPORTS))
    assert win_dll_audit.unresolved(str(dll)) == ["vulkan-1.dll"]
    # Windows file names are case-insensitive; the check must be too.
    (tmp_path / "Vulkan-1.DLL").write_bytes(b"x")
    assert win_dll_audit.unresolved(str(dll)) == []


def test_extra_search_dirs_count_too(tmp_path):
    dll = tmp_path / "mpv-2.dll"
    dll.write_bytes(_pe(_IMPORTS))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "vulkan-1.dll").write_bytes(b"x")
    assert win_dll_audit.unresolved(str(dll), [str(elsewhere)]) == []


def test_the_audit_cli_fails_loud_and_names_the_file(tmp_path, capsys):
    dll = tmp_path / "mpv-2.dll"
    dll.write_bytes(_pe(_IMPORTS))
    assert win_dll_audit.main([str(dll)]) == 1
    out = capsys.readouterr().out
    assert "MISSING  vulkan-1.dll" in out
    assert "::error::mpv-2.dll needs vulkan-1.dll" in out
    (tmp_path / "vulkan-1.dll").write_bytes(b"x")
    assert win_dll_audit.main([str(dll)]) == 0


def test_a_non_pe_file_is_refused(tmp_path):
    bogus = tmp_path / "mpv-2.dll"
    bogus.write_bytes(b"not a dll at all")
    with pytest.raises(ValueError):
        win_dll_audit.imports_of(str(bogus))


# What the 1.2.11 x64 mpv-2.dll (zhongfly mpv-winbuild 2026-08-29) imports,
# read from its import table. Everything but vulkan-1.dll ships with Windows
# 10, and the allowlist must keep saying so: an entry removed from INBOX
# would turn the next release red for a DLL every PC has.
_LIBMPV_1_2_11_IMPORTS = """
    GDI32.dll ole32.dll USER32.dll VERSION.dll vulkan-1.dll ADVAPI32.dll
    SHELL32.dll KERNEL32.dll OLEAUT32.dll api-ms-win-core-winrt-l1-1-0.dll
    ntdll.dll AVRT.dll dwmapi.dll IMM32.dll api-ms-win-core-path-l1-1-0.dll
    SHCORE.dll UxTheme.dll api-ms-win-core-winrt-error-l1-1-1.dll
    OPENGL32.dll api-ms-win-crt-stdio-l1-1-0.dll
    api-ms-win-crt-environment-l1-1-0.dll api-ms-win-crt-runtime-l1-1-0.dll
    api-ms-win-crt-filesystem-l1-1-0.dll api-ms-win-crt-string-l1-1-0.dll
    api-ms-win-crt-private-l1-1-0.dll api-ms-win-crt-locale-l1-1-0.dll
    api-ms-win-crt-time-l1-1-0.dll api-ms-win-crt-convert-l1-1-0.dll
    api-ms-win-crt-utility-l1-1-0.dll api-ms-win-crt-heap-l1-1-0.dll
    api-ms-win-crt-math-l1-1-0.dll api-ms-win-crt-multibyte-l1-1-0.dll
    bcrypt.dll SHLWAPI.dll WS2_32.dll DWrite.dll WLDAP32.dll CRYPT32.dll
    api-ms-win-core-synch-l1-2-0.dll bcryptprimitives.dll AVICAP32.dll
    WINMM.dll SETUPAPI.dll IPHLPAPI.DLL Secur32.dll Normaliz.dll d2d1.dll
""".split()


def test_everything_the_shipped_libmpv_imports_is_inbox_except_the_loader(
        tmp_path):
    dll = tmp_path / "mpv-2.dll"
    dll.write_bytes(_pe(_LIBMPV_1_2_11_IMPORTS))
    assert win_dll_audit.unresolved(str(dll)) == ["vulkan-1.dll"]


# ------------------------------------------------------- the release job ---

def test_the_windows_release_job_picks_exactly_audits_and_pins_the_loader():
    """Read as text on purpose (PyYAML is not a test dependency)."""
    text = (_REPO / ".github/workflows/release.yml").read_text(
        encoding="utf-8")
    # libmpv is the build packaging/build-inputs.json names by its SHA-256
    # (tools/build_inputs.py), never whatever a release API calls newest;
    # tools/pick_mpv_asset.py picks the archive when that list is changed.
    assert 'python tools/build_inputs.py fetch "libmpv-windows-${MPV_ARCH}" libmpv/mpv-2.dll' in text
    assert "releases/latest" not in text and "api.github.com" not in text
    assert "python tools/win_dll_audit.py" in text
    assert re.search(r"VULKAN_LOADER_TAG:\s*v\d+\.\d+\.\d+\s*$", text, re.M), (
        "the Vulkan loader must be built from a pinned tag")
    assert "in x['name']" not in text, (
        "the substring asset match is back - it also matches the v3 build")
