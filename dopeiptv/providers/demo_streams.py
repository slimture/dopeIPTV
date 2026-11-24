"""The demo's streams in one table: free, legal test streams and the Blender
Foundation's open films, each at the address its owner publishes it.

Nothing else of the demo comes over the network - its logos and posters
are drawn by the app, its guide is made here (providers/demo.py). A
maintainer asks every address with ``tools/check_demo.py`` before a release
(docs/MAINTAINING.md); docs/FEATURES.md and the transparency page list the
hosts. Pure data, standard library only: the tool reads this file without
the app.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: Live test streams, on the air around the clock.
LIVE: dict[str, str] = {
    # Akamai's 24/7 live test stream.
    "akamai": "https://cph-p2p-msl.akamaized.net/hls/live/2000341/test/master.m3u8",
    # Unified Streaming's live demo.
    "unified": "https://demo.unified-streaming.com/k8s/live/stable/live.isml/.m3u8",
}

#: The Blender Foundation's open films (CC BY 3.0), as test streams.
FILMS: dict[str, str] = {
    # Big Buck Bunny (2008), Mux's test copy.
    "bbb": "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
    # Sintel (2010), Bitmovin's test copy.
    "sintel": "https://cdn.bitmovin.com/content/assets/sintel/hls/playlist.m3u8",
    # Tears of Steel (2012), Unified Streaming's test copy.
    "tos": ("https://demo.unified-streaming.com/k8s/features/stable/video/"
            "tears-of-steel/tears-of-steel.ism/.m3u8"),
}

#: Test patterns.
TEST: dict[str, str] = {
    # Apple's HLS example (fMP4).
    "bipbop": ("https://devstreaming-cdn.apple.com/videos/streaming/examples/"
               "img_bipbop_adv_example_fmp4/master.m3u8"),
}

#: Every stream of the demo by its key.
STREAMS: dict[str, str] = {**LIVE, **FILMS, **TEST}


def hosts() -> list[str]:
    """The hosts the demo's streams are on - what playing them reaches."""
    return sorted({urlsplit(u).hostname or "" for u in STREAMS.values()} - {""})
