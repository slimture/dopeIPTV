"""Which of a source's addresses are played.

A channel's address is the provider's word - a line of its playlist, a
portal's answer - and it goes to mpv, to the recorder and to an external
player as it is. Each takes far more than streams there:

- ``av://lavfi:`` builds an FFmpeg filter graph, and a graph can write a
  file wherever it is told to (``metadata=mode=print:file=...``, with words
  of the sender's choice in it);
- ``edl://``, ``memory://`` and ``hex://`` carry a whole edit list or file
  in the address; ``lavf://`` and ``ffmpeg://`` reach every FFmpeg protocol;
- ``gopher://`` sends bytes of the sender's choice to any host and port,
  this computer's own too; ``fdclose://`` closes one of the app's own files;
- a line that begins with ``-`` is an option to a player started with it
  (``--script=...`` runs a script).

mpv refuses these from a playlist it reads itself - but the app reads the
playlist and hands mpv one address at a time, which mpv takes as the
person's own. So an address from a source is looked at first
(:func:`refusal`):

- from the network (an Xtream or Stalker provider, a box, a media server, a
  playlist at a web address): a stream over one of the network's media
  protocols (:data:`NETWORK`) and nothing else - no file, no disc or
  device of this computer, and no ``ytdl://`` (yt-dlp run on a page of the
  sender's choice);
- from a playlist file on this computer: anything but what is no stream at
  all (:data:`NEVER`, the ``lavfi`` device, an option, control characters) -
  files anywhere, a disc, a tuner card and a capture device stay the
  person's own to list.

A refused address is no address: the channel stays in its list and does
not play, as one the source gave no address for. Nothing here asks the
network or the disk.
"""

from __future__ import annotations

from .log import log

#: The network's media protocols as mpv names them: what a provider's
#: stream comes over. Not ``ytdl://``: that runs yt-dlp on whatever page
#: follows it - the app's own word for a web stream the person opened
#: (core/ytdlp.mpv_address, which never passes a source), never a source's.
NETWORK = frozenset((
    "http", "https", "httpproxy", "rtsp", "rtsps", "rtmp", "rtmpe", "rtmps",
    "rtmpt", "rtmpte", "rtmpts", "rtp", "srtp", "udp", "mms", "mmsh",
    "mmshttp", "mmst", "srt", "rist", "ftp", "sftp", "smb", "tcp", "tls",
    "dav", "davs", "webdav", "webdavs", "ipfs", "ipns"))

#: No stream, whoever lists it: an address that carries a file or an edit
#: list in itself, reaches every FFmpeg protocol or a socket of this
#: computer, sends the sender's bytes to a port, or touches the app's own
#: open files.
NEVER = frozenset((
    "edl", "memory", "hex", "lavf", "ffmpeg", "gopher", "gophers", "unix",
    "fd", "fdclose", "slice", "md5", "null", "data"))

#: FFmpeg's devices behind av:// and avdevice://. One of them is no device:
#: lavfi is a filter graph, which reads and writes files.
_DEVICE_SCHEMES = frozenset(("av", "avdevice"))
_GRAPH_DEVICES = frozenset(("lavfi",))


def scheme_of(address: str) -> str:
    """The protocol of *address* as mpv reads it, lower case: a letter, then
    letters, digits, ``+``, ``-`` or ``.``, before ``://``. "" for a path."""
    head, sep, _rest = address.partition("://")
    if not sep or not head or not head[0].isascii() or not head[0].isalpha():
        return ""
    if not all(c.isascii() and (c.isalnum() or c in "+-.") for c in head):
        return ""
    return head.lower()


def _device(address: str) -> str:
    """The FFmpeg device an av:// address names ("lavfi" in
    av://lavfi:testsrc), lower case."""
    return address.partition("://")[2].partition(":")[0].strip().lower()


def refusal(address: str, remote: bool = True) -> str:
    """Why *address* is not played, in a word for the log - "" when it is.
    *remote*: it came over the network (anything but a playlist file on
    this computer)."""
    if not address:
        return ""
    if any(c < " " or c == "\x7f" for c in address):
        return "control characters"
    if address.lstrip().startswith("-"):
        return "an option"
    scheme = scheme_of(address)
    if scheme in NEVER:
        return f"{scheme}://"
    if scheme in _DEVICE_SCHEMES and (remote or _device(address) in _GRAPH_DEVICES):
        return f"{scheme}://{_device(address)}"
    if not remote or scheme in NETWORK:
        return ""
    return f"{scheme}://" if scheme else "no network address"


#: Each refusal is said once a session: a list asks for its addresses often.
_said: set[tuple[str, str]] = set()
_SAID_MAX = 200


def played(address: object, remote: bool = True, what: str = "source") -> str:
    """*address* when a source's stream may be that, else "" - logged once,
    with the reason and never the address (it may carry a login)."""
    text = str(address or "")
    why = refusal(text, remote)
    if not why:
        return text
    key = (what, why)
    if key not in _said and len(_said) < _SAID_MAX:
        _said.add(key)
        log.warning("%s: an address that is no stream (%s) is not played", what, why)
    return ""
