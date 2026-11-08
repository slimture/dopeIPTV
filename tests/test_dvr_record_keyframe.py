"""A recording from the buffer begins on a key frame, with the stream's
PAT and PMT before it: one that begins between two key frames shows
nothing - black or grey - until the next."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from dopeiptv.core.dvr_record import TS, KeyframeStart


def _pkt(pid: int, payload: bytes = b"", pusi: bool = False,
         rai: bool = False) -> bytes:
    head = bytes([0x47, (0x40 if pusi else 0) | (pid >> 8), pid & 0xFF])
    if rai:
        body = bytes([0x30, 1, 0x40]) + payload      # adaptation + payload
    else:
        body = bytes([0x10]) + payload
    return (head + body).ljust(TS, b"\xff")[:TS]


def _psi(pid: int, table_id: int, body: bytes) -> bytes:
    length = len(body) + 4                            # + CRC (not checked)
    sec = bytes([table_id, 0xB0 | (length >> 8), length & 0xFF]) + body
    return _pkt(pid, b"\x00" + sec + b"\0\0\0\0", pusi=True)


PAT = _psi(0, 0x00, bytes([0, 1, 0xC1, 0, 0]) + bytes([0, 1, 0xE0, 0x20]))
PMT = _psi(0x20, 0x02, bytes([0, 1, 0xC1, 0, 0, 0xE1, 0x00, 0xF0, 0])
           + bytes([0x1B, 0xE1, 0x00, 0xF0, 0])       # H.264 on 0x100
           + bytes([0x0F, 0xE1, 0x01, 0xF0, 0]))      # AAC on 0x101


def test_it_starts_on_the_video_key_frame_with_pat_and_pmt_first():
    mid_gop = _pkt(0x100, b"P", pusi=True)
    audio_start = _pkt(0x101, b"A", pusi=True, rai=True)
    key = _pkt(0x100, b"I", pusi=True, rai=True)
    after = _pkt(0x101, b"a")
    stream = mid_gop + PAT + audio_start + PMT + mid_gop + key + after
    k = KeyframeStart()
    out = k.feed(stream[:500]) + k.feed(stream[500:]) + k.flush()
    assert out == PAT + PMT + key + after, "not from an audio frame either"
    assert k.skipped == 5 * TS
    assert k.feed(b"more") == b"more"


def test_a_stream_without_markers_or_off_packets_is_written_whole():
    k = KeyframeStart()
    data = PAT + PMT + _pkt(0x100, b"P", pusi=True) * 3
    assert k.feed(data) == b""
    assert k.flush() == data, "held back, then written whole at the end"
    k = KeyframeStart()
    assert k.feed(b"\x00" * 400) == b"\x00" * 400


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="needs ffmpeg")
def test_a_real_stream_cut_mid_gop_plays_from_its_first_frame(tmp_path):
    src = tmp_path / "src.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x240:rate=25:duration=6", "-f", "lavfi",
                    "-i", "sine=duration=6", "-c:v", "libx264", "-g", "50",
                    "-c:a", "aac", "-f", "mpegts", str(src)], check=True)
    data = src.read_bytes()
    cut = data[(len(data) // 3) // TS * TS:]          # mid-GOP
    k = KeyframeStart()
    out = b"".join(k.feed(cut[i:i + 65536])
                   for i in range(0, len(cut), 65536)) + k.flush()
    assert 0 < k.skipped < len(cut)
    rec = tmp_path / "rec.ts"
    rec.write_bytes(out)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
         "-show_entries", "frame=pict_type", "-of", "csv=p=0", str(rec)],
        capture_output=True, text=True)
    types = [t for t in probe.stdout.split() if t]
    assert types and types[0] == "I", types[:5]
    assert "error" not in probe.stderr.lower(), probe.stderr[-300:]
