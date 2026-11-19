"""A source's id as part of a file's name (AGENTS.md rule 5).

A source's cached guide, its lists and its stable-id map are named by its
id (``epg_<id>.xml``, ``lists_<id>.json``, ``m3u_ids_<id>.json``). The app
makes ids of eight hex digits, or "default", but an id also comes from
outside - a backup being restored, settings sync, a playlist file - and one
such as "a/../../elsewhere" named a file outside the cache folder: the
provider's answer written there, a guide read from there.

``file_part`` is the id when it is plainly one - letters, digits, "_", "-",
"." (not dots alone), at most 64 of them - so every id the app made names
the very file it always named and nothing is lost or kept twice; else a
hash of it behind a "~", which no plain id has: the same id the same name
every time, and never a separator, a drive or a name of the system's.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_PLAIN = re.compile(r"(?!\.+$)[A-Za-z0-9_.-]{1,64}")


def file_part(pid: Any) -> str:
    """*pid* as it may stand in a file's name in the cache folder."""
    text = str(pid)
    if _PLAIN.fullmatch(text):
        return text
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
    return "~" + digest[:24]
