"""A row's picture is its source's word. A logo called /dev/zero was read
until the memory was gone, //host/share/logo.png on Windows logs in at that
host with the person's account, an SVG logo made Qt open the files it
named, a picture that says it is 30000 pixels wide takes gigabytes to
decode and a download that never ends filled the memory.

The image loader reads a file as a picture only from where the app's own
pictures are (core/picture_files.py), an SVG from outside is screened
before Qt draws it (core/svg_safe.py), and a picture's download and its
decoded size have a ceiling (core/workers.py)."""
from __future__ import annotations

import base64
import gzip
import os
import stat
import sys
import threading
import time

import pytest

from dopeiptv.core import picture_files, svg_safe


@pytest.fixture(autouse=True)
def _own_folders():
    """Each test names its own trusted folders."""
    saved = dict(picture_files._roots)
    picture_files._roots.clear()
    yield
    picture_files._roots.clear()
    picture_files._roots.update(saved)


# -- files: only from where the app's own pictures are --------------------------------

def test_a_file_is_a_picture_only_from_the_apps_own_folders(tmp_path):
    cache, music, other = tmp_path / "cache", tmp_path / "music", tmp_path / "other"
    for d in (cache / "thumbs", music / "Album", other):
        d.mkdir(parents=True)
    frame = cache / "thumbs" / "a.jpg"
    cover = music / "Album" / "cover.jpg"
    stray = other / "x.jpg"
    for f in (frame, cover, stray):
        f.write_bytes(b"\xff\xd8picture")
    picture_files.trust("cache", [str(cache)])
    picture_files.trust("local", [str(music)])
    assert picture_files.read(str(frame)) == b"\xff\xd8picture"
    assert picture_files.read(str(cover)) == b"\xff\xd8picture"
    assert picture_files.in_group("local", str(cover)) and not picture_files.in_group("cache", str(cover))
    for path in (str(stray), "/etc/passwd", "/dev/zero", "/dev/urandom", "/proc/self/environ",
                 str(music / "Album" / ".." / ".." / "other" / "x.jpg"), str(music), "",
                 str(music) + "-private/secret.jpg", "//evil.example/share/logo.png",
                 "relative.png", str(cover) + "\x00.png"):
        assert not picture_files.trusted(path), path
        assert picture_files.read(path) is None, path
    # A group named again replaces its folders; one emptied is gone.
    picture_files.trust("local", [str(other)])
    assert picture_files.read(str(cover)) is None and picture_files.read(str(stray)) is not None
    picture_files.trust("local", [])
    assert picture_files.read(str(stray)) is None


def test_only_a_plain_file_of_a_pictures_size_is_read(tmp_path):
    picture_files.trust("cache", [str(tmp_path)])
    big = tmp_path / "big.jpg"
    big.write_bytes(b"x" * 5000)
    assert picture_files.read(str(big), limit=4999) is None
    assert picture_files.read(str(big), limit=5000) == b"x" * 5000
    (tmp_path / "dir.png").mkdir()
    assert picture_files.read(str(tmp_path / "dir.png")) is None
    assert picture_files.read(str(tmp_path / "gone.png")) is None
    if hasattr(os, "mkfifo"):
        pipe = tmp_path / "pipe.png"
        os.mkfifo(pipe)
        assert stat.S_ISFIFO(os.stat(pipe).st_mode)
        done: list = []
        t = threading.Thread(target=lambda: done.append(picture_files.read(str(pipe))), daemon=True)
        t.start()
        t.join(5)
        assert done == [None], "reading a pipe waited for someone to write to it"
    if os.path.exists("/dev/zero"):
        link = tmp_path / "endless.png"
        os.symlink("/dev/zero", link)
        assert picture_files.read(str(link)) is None


def test_an_address_names_a_file_as_the_loader_always_read_them():
    assert picture_files.local_path("/a/b.png") == "/a/b.png"
    assert picture_files.local_path("file:///a/b.png") == "/a/b.png"
    assert picture_files.local_path("file://C:\\picons\\a.png") == "C:\\picons\\a.png"
    for web in ("http://x/a.png", "https://x/a.png", "", None, "a.png", "ftp://x/a.png"):
        assert picture_files.local_path(web) is None


# -- SVG: what a picture names outside itself ----------------------------------------

SVG_NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
LOGO = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!-- a logo -->\n<svg {SVG_NS} width="8" height="8">'
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#f00"/></linearGradient>'
        '<linearGradient id="h" xlink:href="#g"/></defs>'
        '<rect id="r" width="8" height="8" fill="url(#h)"/><use xlink:href="#r"/></svg>').encode()


def test_a_picture_that_names_nothing_else_is_handed_on_as_it_came():
    assert svg_safe.screened(LOGO) is LOGO
    doctype = LOGO.replace(b"<svg ", b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
                                     b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n<svg ', 1)
    assert svg_safe.screened(doctype) is doctype
    latin = LOGO.replace(b"UTF-8", b"ISO-8859-1")
    assert svg_safe.screened(latin) is latin
    assert svg_safe.screened(gzip.compress(LOGO)) == LOGO, "an .svgz, unpacked: what was looked at"
    # What is no SVG is not this module's to judge: as it came.
    for other in (b"", b"\x89PNG\r\n\x1a\n<svg", b"GIF89a<image href='/x'>", b"/* XPM */ <", b"P1 <",
                  gzip.compress(b"\x89PNG not markup")):
        assert svg_safe.screened(other) is other and not svg_safe._markup(other)
    # Markup however it begins - Qt strips what it takes for white space.
    for lead in (b"", b"\xef\xbb\xbf", b" \t\r\n", "\u00a0\u2003\u3000".encode(), b"\x1c\x1f"):
        assert svg_safe.maybe_svg(lead + b"<svg/>"), lead
    assert svg_safe.maybe_svg("<svg/>".encode("utf-16")) and svg_safe.maybe_svg("<svg/>".encode("utf-16-le"))
    assert svg_safe.maybe_svg(gzip.compress(b"anything"))


def _svg(body: str, head: str = "") -> bytes:
    return f'{head}<svg {SVG_NS} width="8" height="8">{body}</svg>'.encode()


HOSTILE = {
    "a file by its path": _svg('<image xlink:href="/etc/secret.png" width="8" height="8"/>'),
    "a share on another computer": _svg('<image xlink:href="//evil.example/share/x.png" width="8" height="8"/>'),
    "a plain href": _svg('<image href="/etc/secret.png" width="8" height="8"/>'),
    "a file: address": _svg('<image xlink:href="file:///etc/secret.png" width="8" height="8"/>'),
    "behind character references": _svg('<image xlink:href="&#47;etc&#47;secret.png" width="8" height="8"/>'),
    "behind an entity": _svg('<image xlink:href="&p;" width="8" height="8"/>',
                             '<!DOCTYPE svg [<!ENTITY p "/etc/secret.png">]>'),
    "a whole element behind an entity": _svg("&e;", "<!DOCTYPE svg [<!ENTITY e '<image xlink:href=\"/etc/secret.png\" "
                                                    "width=\"8\" height=\"8\"/>'>]>"),
    "a stylesheet": _svg('<rect width="8" height="8"/>', '<?xml-stylesheet type="text/css" href="/etc/secret.png"?>'),
    "a stylesheet behind an entity": _svg("&s;<rect width='8' height='8'/>",
                                          "<!DOCTYPE svg [<!ENTITY s '&#60;?xml-stylesheet type=\"text/css\" "
                                          "href=\"/etc/secret.png\"?&#62;'>]>"),
    "another prefix for xlink": _svg('<image x:href="/etc/secret.png" width="8" height="8"/>').replace(
        b"xmlns:xlink", b"xmlns:x"),
}


@pytest.mark.parametrize("what", sorted(HOSTILE))
def test_what_a_picture_names_outside_itself_is_gone_and_the_rest_stays(what):
    out = svg_safe.screened(HOSTILE[what])
    assert out is not None and out is not HOSTILE[what]
    assert b"secret" not in out and b"evil.example" not in out and b"xml-stylesheet" not in out, out
    assert b"<!DOCTYPE" not in out and b"<!ENTITY" not in out
    assert out.startswith(b"<svg ") and out.endswith(b"</svg>")
    # And it is again a picture that names nothing else.
    assert svg_safe.names_nothing_else(out), out
    assert svg_safe.screened(gzip.compress(HOSTILE[what])) == out, "the same inside an .svgz"


def test_in_another_encoding_it_is_read_and_written_out_again():
    doc = HOSTILE["a file by its path"].decode()
    for raw in (doc.encode("utf-16"), ('<?xml version="1.0" encoding="UTF-16"?>' + doc).encode("utf-16"),
                ('<?xml version="1.0" encoding="utf-16le"?>' + doc).encode("utf-16-le"),
                ('<?xml version="1.0" encoding="UTF-7"?>' + doc).encode("utf-7")):
        out = svg_safe.screened(raw)
        assert out is None or (b"secret" not in out and out.startswith(b"<svg ")), raw[:40]
    # An encoding the app cannot read is no picture, rather than one handed on unread.
    assert svg_safe.screened(b'<?xml version="1.0" encoding="x-unknown-9"?>' + HOSTILE["a file by its path"]) is None
    assert not svg_safe.names_nothing_else(b'<?xml version="1.0" encoding="UTF-7"?><svg/>')
    assert not svg_safe.names_nothing_else(b"<?xml version='1.0' encoding = 'ebcdic-cp-us' ?><svg/>")


def test_a_picture_carried_in_the_address_stays_and_an_svg_in_it_is_screened_too():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    carried = base64.b64encode(png).decode()
    doc = _svg(f'<image xlink:href="data:image/png;base64,{carried}" width="8" height="8"/>')
    out = svg_safe.screened(doc)
    assert out is not None and carried.encode() in out, "a raster picture in a data: address stays"
    inner = base64.b64encode(HOSTILE["a file by its path"]).decode()
    for typed in ("image/svg+xml", "image/png"):          # what it says it is does not matter
        out = svg_safe.screened(_svg(f'<image xlink:href="data:{typed};base64,{inner}" width="8" height="8"/>'))
        assert out is not None and inner.encode() not in out
        kept = out.split(b"base64,", 1)[1].split(b'"', 1)[0]
        assert b"secret" not in base64.b64decode(kept) and b"<svg" in base64.b64decode(kept)
    # Carried deeper than any picture carries one: not kept.
    deep = HOSTILE["a file by its path"]
    for _ in range(6):
        deep = _svg(f'<image xlink:href="data:image/svg+xml;base64,{base64.b64encode(deep).decode()}" '
                    'width="8" height="8"/>')
    out = svg_safe.screened(deep)
    assert out is not None and b"secret" not in out


def test_entities_that_expand_and_pictures_larger_than_any_are_no_picture():
    laughs = ('<!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">'
              '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;">]>')
    assert svg_safe.screened(_svg("<text>&c;</text>", laughs)) is None
    many = "<!DOCTYPE svg [" + "".join(f'<!ENTITY e{i} "x">' for i in range(400)) + "]>"
    assert svg_safe.screened(_svg("<text>&e1;</text>", many)) is None
    assert svg_safe.screened(b"<svg>" + b" " * (svg_safe.SVG_MAX + 1) + b"</svg>") is None
    bomb = gzip.compress(b"<svg>" + b" " * (svg_safe.SVG_MAX + 10) + b"</svg>")
    assert len(bomb) < 1 << 20 and svg_safe.screened(bomb) is None
    assert svg_safe.screened(gzip.compress(LOGO)[:-12]) is None, "an .svgz cut short"
    # Two gzip members are one file to Qt: both are looked at.
    two = gzip.compress(b"<svg " + SVG_NS.encode() + b">") + gzip.compress(
        b'<image xlink:href="/etc/secret.png"/></svg>')
    out = svg_safe.screened(two)
    assert out is not None and b"secret" not in out and b"<image" in out


def test_an_old_drawing_programs_entities_for_its_namespaces_still_draw():
    doc = ('<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "svg11.dtd" ['
           '<!ENTITY ns_svg "http://www.w3.org/2000/svg"><!ENTITY ns_xlink "http://www.w3.org/1999/xlink">]>'
           '<svg xmlns="&ns_svg;" xmlns:xlink="&ns_xlink;" width="8" height="8">'
           '<style type="text/css"><![CDATA[ .a{fill:#00ff00;} ]]></style>'
           '<rect class="a" width="8" height="8"/><text>A &amp; B &lt; C</text></svg>').encode()
    out = svg_safe.screened(doc)
    assert out is not None
    assert b'xmlns="http://www.w3.org/2000/svg"' in out and b'xmlns:xlink="http://www.w3.org/1999/xlink"' in out
    assert b".a{fill:#00ff00;}" in out and b"A &amp; B &lt; C" in out


# -- with Qt: what is drawn --------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _png(colour: int = 0xFFFF0000, size: int = 8) -> bytes:
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QImage
    img = QImage(size, size, QImage.Format.Format_RGB32)
    img.fill(colour)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def _pixel(img) -> int | None:
    return None if img is None or img.isNull() else img.pixel(4, 4) & 0xFFFFFF


def test_qt_draws_no_file_an_svg_from_outside_names(qapp, tmp_path):
    from dopeiptv.core.workers import decode_image
    red = tmp_path / "red.png"
    red.write_bytes(_png(0xFFFF0000))
    css = tmp_path / "green.css"
    css.write_text("rect { fill: #00ff00; }")
    blue = '<rect width="8" height="8" fill="#0000ff"/>'
    inner = base64.b64encode(_svg(f'{blue}<image xlink:href="{red}" width="8" height="8"/>')).decode()
    cases = {
        "image": _svg(f'{blue}<image xlink:href="{red}" width="8" height="8"/>'),
        "plain href": _svg(f'{blue}<image href="{red}" width="8" height="8"/>'),
        "entity": _svg(f'{blue}<image xlink:href="&p;" width="8" height="8"/>',
                       f'<!DOCTYPE svg [<!ENTITY p "{red}">]>'),
        "stylesheet": _svg(blue, f'<?xml-stylesheet type="text/css" href="{css}"?>'),
        "carried svg": _svg(f'{blue}<image xlink:href="data:image/png;base64,{inner}" width="8" height="8"/>'),
        "utf-16": _svg(f'{blue}<image xlink:href="{red}" width="8" height="8"/>').decode().encode("utf-16"),
        "svgz": gzip.compress(_svg(f'{blue}<image xlink:href="{red}" width="8" height="8"/>')),
    }
    for what, doc in cases.items():
        # A picture of the person's own choosing is drawn as Qt always drew it:
        # this is what the file on disk does to the picture.
        assert _pixel(decode_image(doc, own=True)) in (0xFF0000, 0x00FF00), what
        # From outside: the rectangle of its own, and nothing from the disk.
        assert _pixel(decode_image(doc)) == 0x0000FF, what
    # A raster picture carried in the address is still drawn.
    carried = base64.b64encode(_png(0xFFFF0000)).decode()
    doc = _svg(f'{blue}<image xlink:href="data:image/png;base64,{carried}" width="8" height="8"/>')
    assert _pixel(decode_image(doc)) == 0xFF0000


def test_the_apps_own_flags_draw_the_same_written_out_again(qapp):
    """What the screen writes out for a picture that names something is the
    same picture: every bundled flag, through it, pixel for pixel."""
    from pathlib import Path

    import dopeiptv
    from dopeiptv.core.workers import decode_image
    flags = sorted((Path(dopeiptv.__file__).parent / "assets" / "flags").glob("*.svg"))
    assert len(flags) > 100
    differing = []
    for f in flags:
        raw = f.read_bytes()
        again = svg_safe._written_again(svg_safe._text(raw))
        a, b = decode_image(raw, own=True), decode_image(again, own=True)
        if a is None or b is None or a != b:
            differing.append(f.name)
    assert not differing, differing[:10]


def test_base64_is_read_as_qt_reads_it(qapp):
    from PyQt6.QtCore import QByteArray
    for text in ("aGVsbG8gd29ybGQ=", "aGVsbG8gd29ybGQ", "aGVs bG8g\nd29y bGQ=", "QQ==QUJD", "QUJD=QUJD",
                 "a", "ab", "abc", "", "!!!!", "aGVsbG8*d29ybGQ=", "PHN2Zz4=PHN2Zz4=", "a-b_c+d/e", "é¤aGVsbG8="):
        want = bytes(QByteArray.fromBase64(text.encode("latin-1", "replace")))
        assert svg_safe._qt_base64(text) == want, text


def test_a_picture_from_outside_that_says_it_is_enormous_is_not_decoded(qapp):
    import struct
    import zlib

    from PyQt6.QtGui import QImageReader

    from dopeiptv.core import workers

    def bomb(w: int, h: int) -> bytes:
        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(b"\x00" * (w * 4 + 1))) + chunk(b"IEND", b""))
    assert QImageReader.allocationLimit() == workers.PICTURE_ALLOC_MIB == 256, \
        "Qt's own ceiling, set by the app and not left to a default or the environment"
    for w, h in ((30000, 30000), (16000, 16000), (8000, 8000), (6000, 6000)):
        assert workers.decode_image(bomb(w, h)) is None, (w, h)
        assert not workers.is_picture(bomb(w, h))
    assert workers.decode_image(_png()) is not None
    # A file on this computer (a camera's picture as a cover) is held to
    # Qt's own ceiling alone, as it always was.
    camera = bomb(6000, 6000)
    assert workers._outside(camera) is None and workers._outside(camera, sized=False) is camera
    big_svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="60000" height="60000"><rect width="8" height="8"/></svg>'
    assert workers.decode_image(big_svg) is None


class _Answer:
    status_code = 200

    def __init__(self, body=b"", length=None, endless=False):
        self.headers = {"Content-Type": "image/svg+xml"}
        if length is not None:
            self.headers["Content-Length"] = str(length)
        self.body, self.endless, self.closed, self.read = body, endless, False, 0

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        if self.endless:
            while True:
                self.read += n
                yield b"\x00" * n
        self.read += len(self.body)
        yield self.body

    def close(self):
        self.closed = True


def test_a_pictures_size_is_read_from_its_first_bytes(qapp):
    """Read from the bytes, before any reader is made for them: a picture
    larger than the app decodes is never handed to Qt's plugins."""
    from pathlib import Path

    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QImage

    from dopeiptv.core import workers
    img = QImage(37, 21, QImage.Format.Format_ARGB32)
    img.fill(0xFF336699)
    for fmt in ("PNG", "JPEG", "BMP", "WEBP", "GIF"):
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        if not img.save(buf, fmt):
            continue                    # a Qt without that writer
        assert picture_files.picture_size(bytes(buf.data())) == (37, 21), fmt
    gif = b"GIF89a" + (65535).to_bytes(2, "little") + (65535).to_bytes(2, "little") + b"\x00" * 20
    assert picture_files.picture_size(gif) == (65535, 65535)
    assert workers.decode_image(gif) is None, "17 GiB to decode"
    for junk in (b"", b"\x89PNG", b"\xff\xd8", b"\xff\xd8\xff", b"BM", b"RIFF\x00\x00\x00\x00WEBPVP8 ", b"<svg/>",
                 b"\xff\xd8" + b"\xff\xe0\x00\x02" * 5000):
        assert picture_files.picture_size(junk) is None
    src = Path(workers.__file__).read_text(encoding="utf-8")
    assert src.count(".canRead(") == 1, "a reader's handler is found in ready() alone"
    assert "if ready(reader)" in src, "_reader finds the handler before read() lets go of the GIL"


def test_pictures_are_decoded_on_several_threads_at_once(qapp):
    """Many at a time, as a list of logos is: none waits for another for good."""
    from dopeiptv.core import workers
    pictures = [_png(), _svg('<rect width="8" height="8" fill="#0000ff"/>'),
                _svg('<rect width="8" height="8" fill="#0000ff"/><image xlink:href="/etc/x.png"/>'), LOGO]
    done: list[int] = []

    def work() -> None:
        for _ in range(60):
            for data in pictures:
                assert workers.decode_image(data, 32) is not None
                assert workers.is_picture(data)
        done.append(1)
    threads = [threading.Thread(target=work, daemon=True) for _ in range(6)]
    for t in threads:
        t.start()
    end = time.monotonic() + 60
    for t in threads:
        t.join(max(0.1, end - time.monotonic()))
    assert len(done) == 6, "decoding stopped: two threads wait for each other"


def test_a_pictures_download_has_a_ceiling(qapp):
    from dopeiptv.core import workers
    assert workers.read_capped(_Answer(b"abc")) == b"abc"
    endless = _Answer(endless=True)
    with pytest.raises(workers.PictureTooLarge):
        workers.read_capped(endless, limit=1 << 20)
    assert endless.closed and endless.read <= (1 << 20) + (128 << 10)
    said = _Answer(b"x", length=10 << 30)
    with pytest.raises(workers.PictureTooLarge):
        workers.read_capped(said)
    assert said.closed and said.read == 0, "told it is too large: nothing read"


def _drain(pool, app, until, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        pool.waitForDone(20)
        app.processEvents()
        if until():
            for _ in range(3):
                app.processEvents()
            return
    raise AssertionError("the loader did not answer in time")


def _loader(tmp_path):
    from PyQt6.QtCore import QThreadPool

    from dopeiptv.core.workers import LogoLoader
    pool = QThreadPool()
    pool.setMaxThreadCount(2)
    return pool, LogoLoader(pool, max_size=64, cache_dir=tmp_path / "cache", disk_budget=0)


def test_the_loader_reads_no_file_a_sources_row_names(qapp, tmp_path, monkeypatch):
    from dopeiptv.core import workers
    pool, loader = _loader(tmp_path)
    (tmp_path / "cache").mkdir(exist_ok=True)
    secret = tmp_path / "private.png"
    secret.write_bytes(_png())
    opened: list[str] = []
    real_read = picture_files.read_file
    monkeypatch.setattr(picture_files, "read_file", lambda p, *a: opened.append(p) or real_read(p, *a))
    asked: list = []
    monkeypatch.setattr(workers, "_image_get", lambda u: lambda *a, **k: asked.append(u) or _Answer(_png()))
    got: dict[str, list] = {}
    hostile = ["/dev/zero", "file:///dev/zero", "file:///dev/urandom", str(secret), "file://" + str(secret),
               "//evil.example/share/logo.png", "file:////evil.example/share/logo.png",
               str(tmp_path / "cache" / ".." / "private.png")]
    for url in hostile:
        loader.get(url, lambda pm, u=url: got.setdefault(u, []).append(pm))
    _drain(pool, qapp, lambda: not loader.waiting)
    assert opened == [] and asked == [], "a file outside the app's own folders was opened"
    assert got == {}, "nothing to draw: the row keeps its placeholder"
    assert all(loader.is_dead(u) for u in hostile), "and it is not asked for again at every paint"
    # What the app made, where it keeps it, is drawn as before.
    frame = tmp_path / "cache" / "frame.png"
    frame.write_bytes(_png())
    loader.get(str(frame), lambda pm: got.setdefault("frame", []).append(pm))
    _drain(pool, qapp, lambda: "frame" in got)
    assert not got["frame"][0].isNull() and opened == [str(frame)]


def test_a_cover_in_local_files_and_a_picon_are_still_drawn(qapp, tmp_path):
    pool, loader = _loader(tmp_path)
    music, picons = tmp_path / "Music" / "Album", tmp_path / "picons"
    music.mkdir(parents=True)
    picons.mkdir()
    cover, picon = music / "cover.jpg", picons / "svt1.png"
    cover.write_bytes(_png())
    picon.write_bytes(_png())
    got: dict[str, list] = {}
    for name, path in (("cover", cover), ("picon", picon)):
        loader.get(str(path), lambda pm, n=name: got.setdefault(n, []).append(pm))
    _drain(pool, qapp, lambda: not loader.waiting)
    assert got == {}, "not before the app knows these folders"
    loader.dead.clear()
    picture_files.trust("local", [str(tmp_path / "Music")])
    picture_files.trust("picons", [str(picons)])
    for name, path in (("cover", cover), ("picon", "file://" + str(picon))):
        loader.get(str(path), lambda pm, n=name: got.setdefault(n, []).append(pm))
    _drain(pool, qapp, lambda: len(got) == 2)
    assert not got["cover"][0].isNull() and not got["picon"][0].isNull()


def test_the_window_names_its_folders_to_the_loader(tmp_path):
    """Local files' folders and where the system mounts shares; Nerd's
    picon folder with its setting."""
    from dopeiptv.core import nerd
    from dopeiptv.ui.mw_local import _LocalFilesMixin
    from tests.test_nerd import S

    class W(_LocalFilesMixin):
        def __init__(self):
            self.settings = S()
    w = W()
    w._save_local_dirs([str(tmp_path / "Films")])
    assert picture_files.trusted(str(tmp_path / "Films" / "a" / "poster.jpg"))
    assert not picture_files.trusted(str(tmp_path / "Other" / "poster.jpg"))
    if sys.platform.startswith("linux") and os.environ.get("USER"):
        assert picture_files.trusted(f"/media/{os.environ['USER']}/USB/cover.jpg")
    w._save_local_dirs([])
    assert not picture_files.trusted(str(tmp_path / "Films" / "a" / "poster.jpg"))
    try:
        nerd.install(S(nerd_enabled="true", nerd_picon_dir=str(tmp_path / "picons")))
        assert picture_files.in_group("picons", str(tmp_path / "picons" / "svt1.png"))
        nerd.install(S(nerd_enabled="false", nerd_picon_dir=str(tmp_path / "picons")))
        assert not picture_files.trusted(str(tmp_path / "picons" / "svt1.png"))
    finally:
        nerd.install(None)


def test_a_rows_cover_is_a_web_address_or_a_file_of_the_apps_own(qapp, tmp_path):
    from dopeiptv.services.coverart import CoverArtService
    cover = CoverArtService._provider_cover
    picture_files.trust("cache", [str(tmp_path)])
    own = str(tmp_path / "thumbs" / "a.jpg")
    assert cover({"stream_icon": "http://logo.example/a.png"}) == "http://logo.example/a.png"
    assert cover({"cover": "HTTPS://logo.example/a.png"}) == "HTTPS://logo.example/a.png"
    assert cover({"stream_icon": own}) == own and cover({"stream_icon": "file://" + own}) == "file://" + own
    for hostile in ("file:///dev/zero", "/dev/zero", "file:///home/victim/.ssh/id_rsa", "/etc/passwd",
                    "//evil.example/share/a.png", "ftp://x/a.png", "data:image/png;base64,AAAA",
                    "javascript:alert(1)", "smb://evil.example/share/a.png", "n/A", "", None, 7, ["x"]):
        assert cover({"stream_icon": hostile}) is None, hostile


def test_the_cache_keeps_an_svg_as_it_was_screened(qapp, tmp_path, monkeypatch):
    """More than the loader reads the cache's files - the system's media
    controls, a notification, the phone - so what is kept is what was
    drawn, not what came."""
    from dopeiptv.core import workers
    pool, loader = _loader(tmp_path)
    doc = _svg('<rect width="8" height="8" fill="#0000ff"/><image xlink:href="/etc/secret.png" width="8" height="8"/>')
    monkeypatch.setattr(workers, "_image_get", lambda u: lambda *a, **k: _Answer(doc))
    got: list = []
    url = "https://logo.example/a.svg"
    loader.get(url, got.append)
    _drain(pool, qapp, lambda: bool(got))
    assert not got[0].isNull()
    kept = loader.disk_file(url).read_bytes()
    assert b"secret" not in kept and b"<rect" in kept
    other = "https://logo.example/b.svg"
    path = loader.fetch_file(other)
    assert path is not None and b"secret" not in path.read_bytes()
    # And the phone's small picture of a cached file is decoded the same way.
    from dopeiptv.ui import mw_phone_browse
    planted = tmp_path / "cache" / "old.svg"
    red = tmp_path / "red.png"
    red.write_bytes(_png(0xFFFF0000))
    planted.write_bytes(_svg(f'<rect width="8" height="8" fill="#0000ff"/>'
                             f'<image xlink:href="{red}" width="8" height="8"/>'))
    from PyQt6.QtGui import QImage
    small = QImage.fromData(mw_phone_browse.thumbnail(str(planted)))
    assert not small.isNull() and small.pixel(4, 4) & 0xFF > 200 > (small.pixel(4, 4) >> 16) & 0xFF


def test_an_endless_picture_is_given_up_and_not_asked_for_again(qapp, tmp_path, monkeypatch):
    from dopeiptv.core import workers
    monkeypatch.setattr(workers, "PICTURE_DOWNLOAD_MAX", 1 << 20)
    monkeypatch.setattr(workers.read_capped, "__defaults__", (1 << 20,))
    pool, loader = _loader(tmp_path)
    answers: list[_Answer] = []

    def get(*_a, **kw):
        assert kw.get("stream") is True, "read a chunk at a time, never whole"
        answers.append(_Answer(endless=True))
        return answers[-1]
    monkeypatch.setattr(workers, "_image_get", lambda u: get)
    got: list = []
    url = "https://logo.example/endless.png"
    loader.get(url, got.append)
    _drain(pool, qapp, lambda: bool(got))
    assert got[0].isNull() and answers[0].closed and answers[0].read < 2 << 20
    assert loader.is_dead(url) and loader.disk_file(url) is None


def test_a_notification_shows_a_series_name_as_words():
    from dopeiptv.services.notify import body_text
    assert body_text('New: <a href="http://evil.example">Click</a> & <img src="/etc/x">') == \
        'New: &lt;a href="http://evil.example"&gt;Click&lt;/a&gt; &amp; &lt;img src="/etc/x"&gt;'
    assert body_text("Vikings S2 E5") == "Vikings S2 E5" and body_text("") == ""


def test_the_desktops_media_controls_are_not_pointed_at_a_rows_path(qapp, tmp_path):
    from PyQt6.QtCore import QUrl

    from dopeiptv.services import media_session, mpris
    picture_files.trust("cache", [str(tmp_path)])
    own = tmp_path / "frame.jpg"
    own.write_bytes(_png())
    as_url = QUrl.fromLocalFile(str(own)).toString()
    # Linux: what MPRIS is told.
    assert mpris.art_url("https://logo.example/a.png") == "https://logo.example/a.png"
    assert mpris.art_url(str(own)) == str(own) and mpris.art_url(as_url) == as_url
    # macOS and Windows: the file handed over.
    assert media_session.local_art(str(own)) == str(own)
    assert media_session.local_art(as_url) == str(own)
    for hostile in ("/etc/passwd", "file:///etc/passwd", "smb://evil.example/share/a.png",
                    "//evil.example/share/a.png", "file:////evil.example/share/a.png"):
        assert mpris.art_url(hostile) == "", hostile
        assert media_session.local_art(hostile) is None, hostile


def test_a_link_in_a_trusted_folder_to_elsewhere_is_not_read(tmp_path, monkeypatch):
    """Someone else's link left in a shared picon folder or a share, to a
    file outside the trusted folders: not read. One the person made to a
    file of their own still is."""
    import os

    from dopeiptv.core import picture_files as pf
    trusted = tmp_path / "picons"
    trusted.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    secret = outside / "secret.png"
    secret.write_bytes(b"\x89PNG\r\n\x1a\nsecret")
    (trusted / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nlogo")
    (trusted / "trap.png").symlink_to(secret)
    monkeypatch.setattr(pf, "_roots", {"picons": (str(trusted),)})
    assert pf.read(str(trusted / "logo.png")) == b"\x89PNG\r\n\x1a\nlogo"
    # The person's own link to their own file: read.
    assert pf.read(str(trusted / "trap.png")) == b"\x89PNG\r\n\x1a\nsecret"
    # Another user's link, or one to another user's file: not read.
    real_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real_uid + 1)
    assert pf.read(str(trusted / "trap.png")) is None
    assert pf.read(str(trusted / "logo.png")) == b"\x89PNG\r\n\x1a\nlogo"
    assert pf.read(str(outside / "secret.png")) is None
