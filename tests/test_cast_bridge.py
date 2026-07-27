"""The local bridge that makes a refused channel castable.

Some channels are ordinary H.264 video with Dolby Digital Plus audio: mpv
plays them without blinking, and a Chromecast that is not an Ultra or a
Google TV has no E-AC-3 decoder at all and answers IDLE/ERROR without saying
why. No address and no MIME type changes that - the only way such a channel
reaches the TV is to hand it something it can decode.

These checks run the real HTTP server with a stand-in for ffmpeg, so the
streaming path is exercised without needing a provider or a TV.
"""
import sys
import urllib.request

import pytest

from dopeiptv.providers.cast_bridge import CastBridge, ffmpeg_args, lan_address


def test_the_video_is_copied_when_only_the_audio_is_the_problem():
    """Re-encoding 1080p live would cook the machine, and it is not what the
    receiver choked on."""
    args = ffmpeg_args("ffmpeg", "http://p/9851.m3u8", copy_video=True)
    assert "-c:v" in args and args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "aac"
    assert "libx264" not in args
    assert args[-1] == "pipe:1"


def test_video_is_re_encoded_only_when_it_has_to_be():
    args = ffmpeg_args("ffmpeg", "http://p/x.m3u8", copy_video=False)
    assert args[args.index("-c:v") + 1] == "libx264"
    assert "-preset" in args


def test_hevc_forces_a_video_re_encode():
    b = CastBridge()
    b.exe = sys.executable
    try:
        b.start("http://p/x.m3u8", ["hevc", "eac3"])
        assert b.copy_video is False
        b.start("http://p/x.m3u8", ["h264", "eac3"])
        assert b.copy_video is True
        # An unknown codec is assumed fine: guessing the other way means
        # re-encoding video for no reason, which is the expensive mistake.
        b.start("http://p/x.m3u8", ["h264", "something-new"])
        assert b.copy_video is True
    finally:
        b.stop()


def test_the_chosen_audio_track_is_the_one_mapped():
    args = ffmpeg_args("ffmpeg", "http://p/x.mkv", copy_video=True, audio=2)
    assert "0:a:2" in args
    assert "0:v:0" in args


def test_a_text_subtitle_is_burned_in_through_the_subtitles_filter():
    """The receiver renders no subtitle carried inside a stream - it only
    shows ones handed to it as a separate WebVTT file, which cannot be made
    from a live channel. Burning them into the picture always works, at the
    cost of re-encoding the video."""
    args = ffmpeg_args("ffmpeg", "http://p/x.mkv", copy_video=True,
                       audio=0, subs=1, sub_codec="subrip")
    vf = args[args.index("-vf") + 1]
    assert vf.startswith("subtitles='http\\://p/x.mkv'"), vf
    assert vf.endswith(":si=1"), vf
    # copy_video is overridden: a picture that changes cannot be copied.
    assert args[args.index("-c:v") + 1] == "libx264"


def test_a_bitmap_subtitle_is_overlaid_instead():
    """DVB and PGS subtitles are pictures, and the subtitles filter cannot
    draw them - they are composited over the video."""
    args = ffmpeg_args("ffmpeg", "http://p/x.ts", copy_video=True,
                       audio=0, subs=0, sub_codec="dvb_subtitle")
    assert "-filter_complex" in args
    assert args[args.index("-filter_complex") + 1] == \
        "[0:v:0][0:s:0]overlay[v]"
    assert "[v]" in args


def test_a_source_with_colons_and_quotes_is_escaped():
    args = ffmpeg_args("ffmpeg", "http://h:8080/a'b.mkv", copy_video=False,
                       subs=0, sub_codec="ass")
    vf = args[args.index("-vf") + 1]
    assert "h\\:8080" in vf and "a\\'b" in vf, vf


def test_the_address_is_one_the_chromecast_can_reach():
    addr = lan_address()
    assert addr and not addr.startswith("0."), addr


def test_the_stream_is_served_over_http():
    """End to end through the real server, with a stand-in for ffmpeg: the
    receiver connects, ffmpeg starts, and the bytes come out the other side."""
    b = CastBridge()
    # A "player" that writes a known payload and exits, so the response can
    # be compared byte for byte.
    b.exe = sys.executable
    url = b.start("http://p/x.m3u8", ["h264", "eac3"])
    try:
        import dopeiptv.providers.cast_bridge as cb
        payload = b"MOOV" * 4096

        def fake_args(exe, source, copy_video, *a, **k):
            return [exe, "-c",
                    "import sys;sys.stdout.buffer.write(%r)" % payload]

        real, cb.ffmpeg_args = cb.ffmpeg_args, fake_args
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                assert r.headers["Content-Type"] == "video/mp4"
                assert r.read() == payload
        finally:
            cb.ffmpeg_args = real
    finally:
        b.stop()


def test_a_wrong_address_gets_nothing():
    """The path carries a random token: nothing else on the LAN is served."""
    b = CastBridge()
    b.exe = sys.executable
    url = b.start("http://p/x.m3u8", ["h264"])
    try:
        base = url.rsplit("/", 2)[0]
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{base}/nope/stream.mp4", timeout=10)
        assert e.value.code == 404
    finally:
        b.stop()


def test_stopping_takes_the_server_down():
    b = CastBridge()
    b.exe = sys.executable
    url = b.start("http://p/x.m3u8", ["h264"])
    b.stop()
    assert b.path is None and b.source is None
    with pytest.raises(OSError):        # URLError is an OSError
        urllib.request.urlopen(url, timeout=5)
