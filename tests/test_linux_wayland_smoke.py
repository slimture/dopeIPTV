"""A live process is insufficient: its main Wayland surface must get a buffer."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parent.parent / "tools/linux_wayland_smoke.py"
_SPEC = importlib.util.spec_from_file_location("linux_wayland_smoke", _TOOL)
assert _SPEC is not None and _SPEC.loader is not None
_SMOKE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SMOKE)

_MAIN = """[10.0] -> xdg_wm_base@3.get_xdg_surface(new id xdg_surface@8, wl_surface@7)
[10.1] -> xdg_surface@8.get_toplevel(new id xdg_toplevel@9)
[10.2] -> xdg_toplevel@9.set_title("dopeIPTV")
"""
_ATTACH = "[10.3] -> wl_surface@7.attach(wl_buffer@31, 0, 0)\n"
_COMMIT = "[10.4] -> wl_surface@7.commit()\n"


@pytest.mark.parametrize("separator", ("@", "#"))
def test_a_real_buffer_committed_to_the_titled_main_surface_passes(separator):
    log = "Qt platform: wayland\n" + _MAIN + _ATTACH + _COMMIT
    log += "Embedded playback: enabled\n"
    assert _SMOKE.mapped_main_window(log.replace("@", separator))


@pytest.mark.parametrize("log", (
    "",
    "Qt platform: wayland\nEmbedded playback: enabled\n",
    _MAIN,
    _MAIN + _COMMIT,
    _MAIN + _ATTACH,
    _MAIN + _COMMIT + _ATTACH,
    _MAIN + "wl_surface@7.attach(nil, 0, 0)\n" + _COMMIT,
    _MAIN.replace('"dopeIPTV"', '"Welcome"') + _ATTACH + _COMMIT,
    _MAIN + _ATTACH.replace("@7", "@17") + _COMMIT.replace("@7", "@17"),
    _MAIN + _ATTACH + _COMMIT.replace("@7", "@17"),
    _ATTACH + _COMMIT,
    _MAIN.split("[10.2]", 1)[0] + _ATTACH
    + 'xdg_toplevel@9.set_title("dopeIPTV")\n' + _COMMIT,
))
def test_unmapped_or_unrelated_surfaces_do_not_pass(log):
    assert not _SMOKE.mapped_main_window(log)


def test_a_null_attachment_replaces_the_pending_buffer():
    log = _MAIN + _ATTACH + "wl_surface@7.attach(nil, 0, 0)\n" + _COMMIT
    assert not _SMOKE.mapped_main_window(log)


def test_changing_the_title_drops_a_pending_main_window_buffer():
    log = _MAIN + _ATTACH + 'xdg_toplevel@9.set_title("Welcome")\n' + _COMMIT
    assert not _SMOKE.mapped_main_window(log)


@pytest.mark.parametrize("object_name", (
    "wl_surface@7", "xdg_surface@8", "xdg_toplevel@9",
))
def test_destroyed_objects_do_not_leave_stale_mapping_evidence(object_name):
    log = _MAIN + _ATTACH + f"{object_name}.destroy()\n" + _COMMIT
    assert not _SMOKE.mapped_main_window(log)


def test_reused_object_ids_need_fresh_mapping_evidence():
    old = _MAIN + _ATTACH + "wl_surface@7.destroy()\n"
    unrelated = _MAIN.replace('"dopeIPTV"', '"Welcome"')
    assert not _SMOKE.mapped_main_window(old + unrelated + _ATTACH + _COMMIT)
    assert _SMOKE.mapped_main_window(old + _MAIN + _ATTACH + _COMMIT)
