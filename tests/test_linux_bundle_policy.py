"""Host Mesa must not inherit the build machine's older Wayland libraries.

Execute the spec's actual policy without importing PyInstaller, which is not
installed by the normal test job. Both collection stages matter: Analysis can
bring dependencies back after the explicit libmpv collector has excluded them.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

_SPEC = Path(__file__).resolve().parent.parent / "dopeiptv.spec"
_WAYLAND = ("client", "cursor", "egl", "server")
_HOST_PACKAGES = (
    "libwayland-client0", "libwayland-cursor0",
    "libwayland-egl1", "libwayland-server0",
)


def _tree() -> ast.Module:
    return ast.parse(_SPEC.read_text(encoding="utf-8"), filename=str(_SPEC))


def _apply_policy(platform: str, binaries: list[tuple[str, str, str]]):
    tree = _tree()
    start = next(i for i, node in enumerate(tree.body)
                 if isinstance(node, ast.Assign)
                 and isinstance(node.value, ast.Call)
                 and isinstance(node.value.func, ast.Name)
                 and node.value.func.id == "Analysis")
    end = next(i for i, node in enumerate(tree.body)
               if isinstance(node, ast.Assign)
               and isinstance(node.value, ast.Call)
               and isinstance(node.value.func, ast.Name)
               and node.value.func.id == "PYZ")
    policy = ast.Module(body=tree.body[start + 1:end], type_ignores=[])
    analysis = SimpleNamespace(binaries=binaries)
    exec(compile(policy, str(_SPEC), "exec"), {
        "a": analysis, "os": os, "sys": SimpleNamespace(platform=platform),
    })
    return analysis.binaries


@pytest.mark.parametrize("family", _WAYLAND)
@pytest.mark.parametrize("suffix", ("", ".0", ".1", ".0.21.0"))
@pytest.mark.parametrize("parent", ("", "PyQt6/Qt6/lib/"))
@pytest.mark.parametrize("kind", ("BINARY", "SYMLINK"))
def test_linux_removes_every_wayland_family_and_alias(
        family, suffix, parent, kind):
    name = f"{parent}libwayland-{family}.so{suffix}"
    entry = (name, "/build/host-library", kind)
    assert _apply_policy("linux", [entry]) == []


def test_the_filter_preserves_qt_wayland_and_media_dependencies():
    keep = [
        ("libmpv.so.2", "/build/libmpv.so.2", "BINARY"),
        ("libavcodec.so.58", "/build/libavcodec.so.58", "BINARY"),
        ("libSDL2-2.0.so.0", "/build/libSDL2-2.0.so.0", "BINARY"),
        ("libdecor-0.so.0", "/build/libdecor-0.so.0", "BINARY"),
        ("libQt6WaylandClient.so.6", "/build/qt-client", "BINARY"),
        ("libQt6WaylandCompositor.so.6", "/build/qt-server", "BINARY"),
        ("PyQt6/Qt6/plugins/platforms/libqwayland-generic.so",
         "/build/qt-platform", "BINARY"),
    ]
    host = [
        ("libstdc++.so.6", "/build/libstdc++.so.6", "BINARY"),
        ("libgcc_s.so.1", "/build/libgcc_s.so.1", "BINARY"),
        ("libwayland-client.so.0", "/build/libwayland-client.so.0", "BINARY"),
    ]
    assert _apply_policy("linux", keep + host) == keep


@pytest.mark.parametrize("platform", ("darwin", "win32"))
def test_wayland_filter_does_not_change_other_platforms(platform):
    binaries = [
        (f"libwayland-{family}.so.0", "/build/library", "BINARY")
        for family in _WAYLAND
    ]
    assert _apply_policy(platform, binaries) == binaries


def test_libmpv_collection_leaves_wayland_on_the_host(tmp_path, monkeypatch):
    node = next(node for node in _tree().body
                if isinstance(node, ast.FunctionDef)
                and node.name == "_libmpv_dep_binaries")
    namespace = {"os": os, "shutil": shutil, "tempfile": tempfile}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(_SPEC), "exec"),
         namespace)
    names = [f"libwayland-{family}.so.0" for family in _WAYLAND]
    names += ["libavcodec.so.58", "libSDL2-2.0.so.0", "libdecor-0.so.0"]
    sources = tmp_path / "sources"
    sources.mkdir()
    for name in names:
        (sources / name).write_bytes(b"library")
    output = "\n".join(
        f"{name} => {sources / name} (0x1234)" for name in names)
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: output)
    stage = tmp_path / "stage"
    stage.mkdir()
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kw: str(stage))

    deps = namespace["_libmpv_dep_binaries"]("/build/libmpv.so.2")

    assert {Path(source).name for source, dest in deps} == {
        "libavcodec.so.58", "libSDL2-2.0.so.0", "libdecor-0.so.0",
    }
    assert all(dest == "." for source, dest in deps)
    assert not list(stage.glob("libwayland-*.so*"))


def _linux_release() -> str:
    workflow = (_SPEC.parent / ".github/workflows/release.yml").read_text(
        encoding="utf-8")
    job = workflow.split("  linux-bundle:\n", 1)[1]
    job = job.split("  macos-dmg:\n", 1)[0]
    return "\n".join(line for line in job.splitlines()
                     if not line.lstrip().startswith("#"))


def _step(job: str, name: str) -> str:
    return job.split(f"      - name: {name}\n", 1)[1].split(
        "      - name: ", 1)[0]


def test_debian_package_declares_every_host_wayland_dependency():
    step = _step(_linux_release(), "Build .deb")
    dependencies = set(re.findall(r"--depends\s+(\S+)", step))
    assert set(_HOST_PACKAGES) <= dependencies


def test_clean_distro_checks_install_the_whole_host_wayland_family():
    step = _step(_linux_release(), "Self-containment test across clean distros")
    installs = "\n".join(re.findall(
        r"apt-get install(?:[^\n]*\\\n)*[^\n]*", step))
    for package in _HOST_PACKAGES:
        assert re.search(rf"\b{re.escape(package)}\b", installs), package


def test_debian_13_is_checked_on_both_supported_architectures():
    job = _linux_release()
    distros = {}
    arch = None
    for line in job.splitlines():
        match = re.search(r"- arch:\s*(\S+)", line)
        if match:
            arch = match.group(1)
        match = re.search(r'test_distros:\s*"([^"]+)"', line)
        if match:
            distros[arch] = match.group(1).split()
    assert "debian:13" in distros["x86_64"]
    assert "debian:13" in distros["aarch64"]


def test_release_audits_the_bundle_and_launches_native_wayland():
    job = _linux_release()
    assert "libwayland-*.so*" in job
    assert re.search(r"find\s+dist/dopeiptv", job)
    step = _step(job, "Self-containment test across clean distros")
    assert re.search(r"python3?\s+/work/tools/linux_wayland_smoke\.py", step)
