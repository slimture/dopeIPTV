"""macOS's sound output (core/platform_macos.py): mpv's AVFoundation output
first, CoreAudio after it. mpv's CoreAudio output is handed a device change
after it has gone (hotplug_cb on a freed output) - an AirPlay device found
nearby crashed the app; AVFoundation's listens to no device change."""
from __future__ import annotations

from dopeiptv.core import platform_macos
from dopeiptv.media.embedded import QUIET_AO, ao_text


def test_avfoundation_first_coreaudio_after_it():
    opts = platform_macos.extra_mpv_opts()
    assert opts["ao"] == platform_macos.MAC_AO == "avfoundation,coreaudio"
    assert opts["hwdec"] == "videotoolbox-copy"


def test_the_order_comes_back_after_a_mute():
    # Muted the player opens mpv's null output; unmuted, the order it had.
    as_mpv_says = [{"name": "avfoundation", "params": []}, {"name": "coreaudio", "params": []}]
    assert ao_text(as_mpv_says) == platform_macos.MAC_AO
    assert ao_text([{"name": QUIET_AO}]) == ""
