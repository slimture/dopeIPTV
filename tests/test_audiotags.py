"""Reading tags and cover art straight out of music files.

Files are built byte by byte here rather than committed as binaries, so
the parsers are tested against the real container layouts.
"""
from dopeiptv.core.audiotags import cover_bytes, display_name, read_tags


def _flac(path, fields, picture=b"JPEGDATA"):
    def block(kind, data, last=False):
        return (bytes([kind | (0x80 if last else 0)])
                + len(data).to_bytes(3, "big") + data)
    vendor = b"ref 1.3.2"
    body = len(vendor).to_bytes(4, "little") + vendor
    body += len(fields).to_bytes(4, "little")
    for f in fields:
        body += len(f).to_bytes(4, "little") + f
    pic = (b"\x00\x00\x00\x03" + (10).to_bytes(4, "big") + b"image/jpeg"
           + (0).to_bytes(4, "big") + b"\x00" * 16
           + len(picture).to_bytes(4, "big") + picture)
    path.write_bytes(b"fLaC" + block(4, body) + block(6, pic, last=True))


def _mp3(path, frames):
    body = b""
    for fid, text in frames:
        b = b"\x03" + text.encode()
        body += fid + len(b).to_bytes(4, "big") + b"\x00\x00" + b
    n = len(body)
    sync = bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F,
                  n & 0x7F])
    path.write_bytes(b"ID3\x03\x00\x00" + sync + body)


def test_flac_tags_and_embedded_cover(tmp_path):
    f = tmp_path / "02.flac"
    _flac(f, [b"ARTIST=Kanye West", b"ALBUM=Donda", b"TITLE=Jail",
              b"TRACKNUMBER=2", b"DATE=2021"])
    tags, cover = read_tags(str(f), want_cover=True)
    assert tags["artist"] == "Kanye West"
    assert tags["album"] == "Donda"
    assert tags["title"] == "Jail"
    assert tags["date"] == "2021"
    assert cover == b"JPEGDATA"
    assert display_name(str(f)) == "2. Jail"


def test_id3v2_tags(tmp_path):
    f = tmp_path / "one.mp3"
    _mp3(f, [(b"TPE1", "Daft Punk"), (b"TALB", "Discovery"),
             (b"TIT2", "One More Time"), (b"TRCK", "1/14")])
    tags = read_tags(str(f))[0]
    assert tags["artist"] == "Daft Punk"
    assert tags["album"] == "Discovery"
    assert display_name(str(f)) == "1. One More Time"


def test_untagged_and_unreadable_fall_back_to_the_file_name(tmp_path):
    plain = tmp_path / "Bara en fil.flac"
    plain.write_bytes(b"not really a flac")
    assert read_tags(str(plain))[0] == {}
    assert display_name(str(plain)) == "Bara en fil"
    missing = tmp_path / "finns-inte.mp3"
    assert read_tags(str(missing))[0] == {}
    assert cover_bytes(str(missing)) is None


def test_a_track_with_no_number_keeps_its_title(tmp_path):
    f = tmp_path / "x.flac"
    _flac(f, [b"TITLE=Runaway", b"ARTIST=Kanye West"])
    assert display_name(str(f)) == "Runaway"
