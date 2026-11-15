"""Home Assistant controls the app over MQTT (Nerd > Remote and sharing,
MQTT's "Take commands"): the command topics, what a message on one asks,
and the discovery that puts controls on the app's device - four buttons, a
volume slider, a list of the source's favourite channels and a box to type
a channel's name or number in.

    <prefix>/command/playpause      any payload (Home Assistant's "PRESS")
    <prefix>/command/stop
    <prefix>/command/channel_up
    <prefix>/command/channel_down
    <prefix>/command/volume         0 to 100
    <prefix>/command/favorite       a favourite channel, as the list names it
    <prefix>/command/play           a channel's name, or its number

A retained message is never a command (one left on the broker would run
again at every connect), nor is anything else this does not know: it is
ignored and logged at debug, never what it said. The broker's thread only
reads them; the window carries them out on its own thread (mw_events).
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .log import log

HA_PREFIX = "homeassistant"
BUTTONS: tuple[tuple[str, str, str], ...] = (
    ("playpause", "Play/Pause", "mdi:play-pause"),
    ("stop", "Stop", "mdi:stop"),
    ("channel_up", "Channel up", "mdi:chevron-up"),
    ("channel_down", "Channel down", "mdi:chevron-down"),
)
COMMANDS = frozenset([b[0] for b in BUTTONS] + ["volume", "favorite", "play"])
MAX_TEXT = 100
MAX_OPTIONS = 100
MAX_OPTION_LEN = 80


def command_topic(prefix: str, name: str) -> str:
    return f"{prefix}/command/{name}"


def command_filter(prefix: str) -> str:
    """What the app subscribes to: every command topic, one level deep."""
    return f"{prefix}/command/+"


def _text(payload: bytes) -> str | None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    text = "".join(c for c in text if c.isprintable()).strip()
    return text if 0 < len(text) <= MAX_TEXT else None


def parse(prefix: str, topic: str, payload: bytes, retained: bool = False) -> tuple[str, Any] | None:
    """(command, value) of a message on a command topic, or None: not
    one, retained, or a value it cannot take."""
    head = f"{prefix}/command/"
    name = topic[len(head):] if topic.startswith(head) else ""
    if retained or name not in COMMANDS:
        log.debug("mqtt: a message ignored (%s)", "retained" if retained and name in COMMANDS
                  else "not a command")
        return None
    if name in ("playpause", "stop", "channel_up", "channel_down"):
        return name, None
    text = _text(payload)
    if text is None:
        log.debug("mqtt: %s without a value it can take - ignored", name)
        return None
    if name == "volume":
        try:
            value = float(text)
        except ValueError:
            log.debug("mqtt: volume is not a number - ignored")
            return None
        if value != value:          # NaN
            return None
        return name, int(round(max(0.0, min(100.0, value))))
    return name, text


def favorite_options(rows: Iterable[dict]) -> list[tuple[str, dict]]:
    """The list's options, each a favourite channel's name - the same name
    twice told apart by a number - and the channel it plays."""
    out: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for row in rows:
        name = re.sub(r"\s+", " ", str(row.get("name") or "")).strip()[:MAX_OPTION_LEN]
        if not name or name.lower() == "none":
            continue
        option, n = name, 2
        while option in seen:
            option, n = f"{name} ({n})", n + 1
        seen.add(option)
        out.append((option, row))
        if len(out) >= MAX_OPTIONS:
            break
    return out


def config_topics(node: str) -> list[str]:
    """Every control's discovery topic (to remove them)."""
    return ([f"{HA_PREFIX}/button/{node}/{name}/config" for name, _n, _i in BUTTONS]
            + [f"{HA_PREFIX}/number/{node}/volume/config", f"{HA_PREFIX}/select/{node}/favorite/config",
               f"{HA_PREFIX}/text/{node}/play/config"])


def discovery(node: str, prefix: str, device: dict[str, Any],
              options: Iterable[str] | None) -> list[tuple[str, dict[str, Any] | None]]:
    """The controls' discovery configs, or - for *options* None (commands
    off) - None for each, which removes it (an empty retained message). No
    favourites: no list."""
    topics = config_topics(node)
    if options is None:
        return [(t, None) for t in topics]
    state, avail = f"{prefix}/state", f"{prefix}/availability"
    common = {"availability_topic": avail, "payload_available": "online",
              "payload_not_available": "offline", "device": device}
    out: list[tuple[str, dict[str, Any] | None]] = []
    for (name, label, icon), topic in zip(BUTTONS, topics[:len(BUTTONS)], strict=True):
        out.append((topic, dict(common, name=label, unique_id=f"{node}_{name}", icon=icon,
                                command_topic=command_topic(prefix, name), payload_press="PRESS")))
    out.append((topics[4], dict(common, name="Volume", unique_id=f"{node}_volume", icon="mdi:volume-high",
                                command_topic=command_topic(prefix, "volume"), state_topic=state,
                                value_template="{{ value_json.volume | default(0) }}",
                                min=0, max=100, step=1, mode="slider")))
    opts = list(options)
    if opts:
        out.append((topics[5], dict(common, name="Favorite channel", unique_id=f"{node}_favorite",
                                    icon="mdi:star", command_topic=command_topic(prefix, "favorite"),
                                    state_topic=state, options=opts,
                                    value_template="{{ value_json.favorite | default('None') }}")))
    else:
        out.append((topics[5], None))
    out.append((topics[6], dict(common, name="Play channel", unique_id=f"{node}_play",
                                icon="mdi:television-classic", command_topic=command_topic(prefix, "play"),
                                min=1, max=MAX_TEXT, mode="text")))
    return out
