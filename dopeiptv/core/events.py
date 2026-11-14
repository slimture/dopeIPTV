"""Events pushed out (Nerd > Remote and sharing): when something happens in
the app, a small JSON message goes to the user's own webhooks and MQTT
broker - Home Assistant, Node-RED, a script. The remote API (remote_api.py)
is asked; this tells.

The events, each a JSON object with ``event``, ``time`` (ISO 8601, local
time with its offset) and ``app``:

    app_started         version
    app_quitting
    playback_started    kind (live, movie, episode, recording, local),
                        title, channel, programme, series, source, catchup
    channel_changed     the same, and previous (the channel before) - after
                        playback_started, when a live channel replaced another
    playback_paused / playback_resumed / playback_stopped (reason: stopped,
    ended) / playback_failed   what was playing
    recording_started   title, channel
    recording_finished  title, channel, file (its name), reason
    recording_failed    title, channel, error
    test                the Nerd page's Test

Never a login, a stream's address or a token. While viewing privately
(core/privacy.py) an event says what happened, not what: no title, channel,
programme, series, source or file, and ``"private": true`` - automations
("dim the lights while something plays") keep working, and what is watched
does not end up in the home system's history either.

A webhook gets each event as a POST (application/json, the page's header
for a token); a few tries, 3 s each. MQTT gets each on ``<prefix>/event``
and what plays now, retained, on ``<prefix>/state`` ({"state": "playing" |
"paused" | "idle" | "off", ...}); ``<prefix>/availability`` says online,
and the broker says offline (the last will) when the app goes away. With
Home Assistant discovery on, two sensors appear by themselves.

The connection to the broker is kept open: the last will needs one, so a
crash or a power cut does not leave "playing" behind for ever; no handshake
(TLS) per event; a PINGREQ keeps it alive while idle, and one is answered
before a publish after a quiet spell, so a broker that restarted costs a
reconnect, not the event.

Only ``Dispatcher.emit`` runs on the window's thread, and it only builds
the message and puts it on each receiver's queue (at most QUEUE_MAX, the
oldest go first); each receiver has a thread of its own, so a webhook that
does not answer holds up neither the player nor the broker.
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import urlsplit

from . import mqtt, nerd
from .log import log
from .nerd_check import FAIL, MASK, OK, WARN, Check

QUEUE_MAX = 50
HTTP_TIMEOUT = 3.0
RETRY_WAITS = (1.0, 3.0)          # before the second and the third try
MQTT_KEEPALIVE = 60
MQTT_TIMEOUT = 5.0
VERIFY_IDLE_S = 5.0               # a quiet connection is pinged before a publish
BACKOFF = (0.0, 5.0, 10.0, 30.0, 60.0, 300.0)
DEFAULT_PREFIX = "dopeiptv"
HA_PREFIX = "homeassistant"

#: What an event may say about what plays: none of it while viewing privately.
WHAT = frozenset({"title", "channel", "programme", "series", "source", "previous", "file", "error"})


# -- the settings ----------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    hooks: tuple[str, ...] = ()
    header: tuple[str, str] | None = None
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_tls: bool = False
    mqtt_user: str = ""
    mqtt_pass: str = ""
    client_id: str = ""
    prefix: str = DEFAULT_PREFIX
    ha: bool = False

    @property
    def mqtt(self) -> bool:
        return bool(self.mqtt_host)

    @property
    def on(self) -> bool:
        return bool(self.hooks) or self.mqtt


def hook_urls(text: str) -> tuple[list[str], list[int]]:
    """The http(s) addresses, one a line, and the numbers of the lines
    that are not one (# starts a comment)."""
    good: list[str] = []
    bad: list[int] = []
    for i, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        u = urlsplit(line) if " " not in line else None
        if u is not None and u.scheme in ("http", "https") and u.hostname:
            if line not in good:
                good.append(line)
        else:
            bad.append(i)
    return good, bad


def hook_header(text: str) -> tuple[str, str] | None:
    """``Name: value``, or a token on its own (sent as Authorization: Bearer)."""
    line = (text or "").strip()
    if not line or "\n" in line or "\r" in line:
        return None
    parsed = nerd.parse_headers(line)
    if parsed:
        return next(iter(parsed.items()))
    return ("Authorization", f"Bearer {line}")


def default_client_id() -> str:
    host = re.sub(r"[^A-Za-z0-9-]", "", socket.gethostname().split(".")[0]) or "pc"
    return ("dopeiptv-" + host)[:23]


def clean_prefix(text: str) -> str:
    p = (text or "").strip().strip("/")
    return p if p and mqtt.valid_topic(p + "/event") else DEFAULT_PREFIX


def config(settings: Any) -> Config:
    """What is to be sent, and where, from the settings: nothing while Nerd
    is off, a switch is off or its fields are empty."""
    n = nerd.Nerd(settings)
    if not n.on:
        return Config()
    hooks: tuple[str, ...] = ()
    header = None
    if n.flag("nerd_hooks"):
        hooks = tuple(hook_urls(n.get("nerd_hooks_urls"))[0])
        header = hook_header(n.get("nerd_hooks_header")) if hooks else None
    host = n.get("nerd_mqtt_host").strip() if n.flag("nerd_mqtt") else ""
    if not host:
        return Config(hooks=hooks, header=header)
    port = n.num("nerd_mqtt_port", 1883)
    return Config(hooks=hooks, header=header, mqtt_host=host,
                  mqtt_port=port if 1 <= port <= 65535 else 1883,
                  mqtt_tls=n.flag("nerd_mqtt_tls"), mqtt_user=n.get("nerd_mqtt_user").strip(),
                  mqtt_pass=n.get("nerd_mqtt_pass"),
                  client_id=n.get("nerd_mqtt_client").strip() or default_client_id(),
                  prefix=clean_prefix(n.get("nerd_mqtt_prefix")), ha=n.flag("nerd_mqtt_ha"))


# -- the messages ------------------------------------------------------------------

def iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def payload(event: str, fields: dict[str, Any] | None = None, private: bool = False,
            now: float | None = None) -> dict[str, Any]:
    """An event's message: empty fields left out, and what plays left out
    too while viewing privately."""
    out: dict[str, Any] = {"event": event, "time": iso(time.time() if now is None else now),
                           "app": "dopeIPTV"}
    for k, v in (fields or {}).items():
        if v is None or v == "" or k in out or (private and k in WHAT):
            continue
        out[k] = v
    if private:
        out["private"] = True
    return out


def state_payload(state: str, fields: dict[str, Any] | None = None, private: bool = False,
                  now: float | None = None) -> dict[str, Any]:
    """``<prefix>/state``: playing, paused, idle or off, and what."""
    out = payload("state", fields if state in ("playing", "paused") else None, private, now)
    del out["event"]
    out["state"] = state
    return out


def shown_url(url: str) -> str:
    """A webhook's address as a check shows it: where it goes, not its path
    (Home Assistant's webhook id is the secret)."""
    try:
        u = urlsplit(url)
        host = u.hostname or ""
    except ValueError:
        return MASK
    if ":" in host:
        host = f"[{host}]"
    port = f":{u.port}" if u.port else ""
    rest = "/" + MASK if (u.path not in ("", "/") or u.query) else "/"
    return f"{u.scheme}://{host}{port}{rest}"


def _safe_text(text: Any) -> str:
    """A recording's error, when it says nothing about a file or an address."""
    s = str(text or "").strip()
    if not s or len(s) > 120 or any(c in s for c in "/\\@") or "://" in s:
        return ""
    return s


def _version() -> str:
    try:
        from .. import __version__
        return str(__version__)
    except Exception:
        return ""


# -- the receivers ---------------------------------------------------------------------

class _Sink:
    """A queue and the thread that empties it."""

    name = "sink"

    def __init__(self, limit: int = QUEUE_MAX) -> None:
        self.limit = limit
        self._q: deque[tuple[str, dict[str, Any]]] = deque()
        self._cv = threading.Condition()
        self._closing = False
        self._deadline = 0.0
        self.sent = self.failed = self.dropped = 0
        self._thread = threading.Thread(target=self._loop, name=f"dopeiptv-{self.name}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def put(self, kind: str, message: dict[str, Any]) -> None:
        with self._cv:
            if self._closing:
                return
            if kind == "state":
                # Only the latest state is worth sending.
                kept = [it for it in self._q if it[0] != "state"]
                if len(kept) != len(self._q):
                    self._q = deque(kept)
            if len(self._q) >= self.limit:
                self._q.popleft()
                self.dropped += 1
                log.info("events: %s is behind, the oldest message is dropped (%d so far)",
                         self.name, self.dropped)
            self._q.append((kind, message))
            self._cv.notify()

    def close(self, drain_s: float = 1.0) -> None:
        """Send what is queued for at most *drain_s*, then end."""
        with self._cv:
            self._closing = True
            self._deadline = time.monotonic() + max(0.0, drain_s)
            self._cv.notify()

    def join(self, timeout: float) -> None:
        if self._thread.is_alive():
            self._thread.join(max(0.0, timeout))

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def _left(self) -> float:
        """While closing: the time left to send in."""
        return self._deadline - time.monotonic() if self._closing else 3600.0

    def _pause(self, secs: float) -> bool:
        """Wait between tries (a new message does not cut it short); False
        when closing did."""
        end = time.monotonic() + secs
        with self._cv:
            while not self._closing:
                left = end - time.monotonic()
                if left <= 0:
                    return True
                self._cv.wait(left)
            return False

    def _loop(self) -> None:
        try:
            while True:
                item = None
                with self._cv:
                    if not self._q and not self._closing:
                        self._cv.wait(self._idle_wait())
                    if self._closing and (not self._q or self._left() <= 0):
                        break
                    if self._q:
                        item = self._q.popleft()
                try:
                    if item is not None:
                        self._send(*item)
                    else:
                        self._idle()
                except Exception as e:
                    log.info("events: %s: %s", self.name, e)
        finally:
            try:
                self._finish()
            except Exception as e:
                log.debug("events: %s at the end: %s", self.name, e)

    # -- what each kind does --------------------------------------------------------

    def _idle_wait(self) -> float:
        return 3600.0

    def _idle(self) -> None:
        pass

    def _send(self, kind: str, message: dict[str, Any]) -> None:
        raise NotImplementedError

    def _finish(self) -> None:
        pass


def _no_proxy(host: str) -> bool:
    """A home system is asked directly, whatever proxy the desktop names."""
    import ipaddress
    h = (host or "").lower()
    if h in ("localhost",) or h.endswith((".local", ".lan", ".home", ".internal")) or "." not in h:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def post(url: str, message: dict[str, Any], header: tuple[str, str] | None,
         timeout: float = HTTP_TIMEOUT) -> tuple[int, str]:
    """POST *message* as JSON: (HTTP status, "") or (0, what went wrong)."""
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("User-Agent", f"dopeIPTV/{_version() or '1'}")
    if header is not None:
        req.add_header(header[0], header[1])
    handlers: list[Any] = []
    if _no_proxy(urlsplit(url).hostname or ""):
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=timeout) as r:
            r.read(4096)
            return int(r.status), ""
    except urllib.error.HTTPError as e:
        return int(e.code), ""
    except urllib.error.URLError as e:
        reason = e.reason
        return 0, f"{type(reason).__name__}: {reason}" if isinstance(reason, BaseException) else str(reason)
    except Exception as e:           # no answer, or not HTTP (http.client's own errors)
        return 0, f"{type(e).__name__}: {e}"


def _retry(code: int) -> bool:
    return code == 0 or code >= 500 or code in (408, 429)


class WebhookSink(_Sink):
    """One address: each event POSTed, tried again a couple of times when
    it does not answer or answers with a server error."""

    name = "webhook"

    def __init__(self, url: str, header: tuple[str, str] | None, limit: int = QUEUE_MAX) -> None:
        self.url, self.header = url, header
        super().__init__(limit)

    def _send(self, kind: str, message: dict[str, Any]) -> None:
        if kind != "event":
            return
        code, err = 0, ""
        for attempt in range(len(RETRY_WAITS) + 1):
            if attempt and not self._pause(RETRY_WAITS[attempt - 1]):
                break
            left = self._left()
            if left <= 0:
                break
            code, err = post(self.url, message, self.header, min(HTTP_TIMEOUT, left))
            if 200 <= code < 300:
                self.sent += 1
                return
            if not _retry(code) or self._closing:
                break
        self.failed += 1
        log.info("events: %s did not take %s: %s", shown_url(self.url), message.get("event"),
                 f"HTTP {code}" if code else err)


class MqttSink(_Sink):
    """The broker: one connection kept open, a last will, keepalive."""

    name = "mqtt"

    def __init__(self, cfg: Config, limit: int = QUEUE_MAX, after: Iterable[_Sink] = ()) -> None:
        self.cfg = cfg
        self.prefix = cfg.prefix
        self.client: mqtt.Client | None = None
        self.state: dict[str, Any] | None = None
        self._fails = 0
        self._next_try = 0.0
        self._fresh = False
        # The connection before this one (Settings changed): gone first, so
        # its "offline" never lands after this one's "online".
        self._after = list(after)
        super().__init__(limit)

    def topic(self, leaf: str) -> str:
        return f"{self.prefix}/{leaf}"

    def _make(self) -> mqtt.Client:
        c = self.cfg
        return mqtt.Client(c.mqtt_host, c.mqtt_port, c.client_id, c.mqtt_user, c.mqtt_pass, c.mqtt_tls,
                           keepalive=MQTT_KEEPALIVE, timeout=MQTT_TIMEOUT,
                           will=(self.topic("availability"), b"offline", True))

    def _ensure(self) -> mqtt.Client | None:
        """A connection known to be alive, or None (the broker is away: it
        is tried again later, sooner at first)."""
        cl = self.client
        if cl is not None and cl.connected:
            if time.monotonic() - cl.last_io < VERIFY_IDLE_S:
                return cl
            try:
                cl.ping()
                return cl
            except (OSError, mqtt.MqttError) as e:
                log.info("events: the broker went away (%s) - connecting again", e)
                cl.close(clean=False)
                self.client = None
                self._next_try = 0.0
        if time.monotonic() < self._next_try or self._closing:
            return None
        while self._after:
            self._after.pop().join(MQTT_TIMEOUT)
        cl = self._make()
        try:
            cl.timeout = min(MQTT_TIMEOUT, max(0.5, self._left()))
            cl.connect()
            cl.timeout = MQTT_TIMEOUT
            cl.publish(self.topic("availability"), b"online", retain=True)
            if self.cfg.ha:
                for topic, conf in ha_discovery(self.cfg):
                    cl.publish(topic, json.dumps(conf), retain=True)
            if self.state is not None:
                cl.publish(self.topic("state"), json.dumps(self.state, ensure_ascii=False), retain=True)
        except (OSError, mqtt.MqttError) as e:
            cl.close(clean=False)
            wait = BACKOFF[min(self._fails + 1, len(BACKOFF) - 1)]
            self._fails += 1
            self._next_try = time.monotonic() + wait
            log.info("events: no MQTT broker at %s:%d (%s) - next try in %d s",
                     self.cfg.mqtt_host, self.cfg.mqtt_port, e, int(wait))
            return None
        if self._fails:
            log.info("events: connected to the MQTT broker again")
        else:
            log.info("events: connected to the MQTT broker at %s:%d", self.cfg.mqtt_host, self.cfg.mqtt_port)
        self._fails = 0
        self.client = cl
        self._fresh = True
        return cl

    def _send(self, kind: str, message: dict[str, Any]) -> None:
        if kind == "state":
            self.state = message
        topic = self.topic("state" if kind == "state" else "event")
        data = json.dumps(message, ensure_ascii=False).encode("utf-8")
        for _attempt in range(2):
            cl = self._ensure()
            if cl is None:
                break
            fresh, self._fresh = self._fresh, False
            if fresh and kind == "state":
                self.sent += 1           # a new connection sent it already
                return
            try:
                cl.publish(topic, data, retain=kind == "state")
                self.sent += 1
                return
            except OSError as e:
                log.info("events: publishing failed (%s) - connecting again", e)
                cl.close(clean=False)
                self.client = None
                self._next_try = 0.0
        if kind == "event":
            # The state goes out on the next connection anyway; an event is a
            # moment, not worth sending minutes late.
            self.failed += 1

    def _idle_wait(self) -> float:
        if self.client is not None and self.client.connected:
            return max(1.0, MQTT_KEEPALIVE / 2 - (time.monotonic() - self.client.last_io))
        return max(1.0, self._next_try - time.monotonic())

    def _idle(self) -> None:
        cl = self.client
        if cl is not None and cl.connected:
            if time.monotonic() - cl.last_io >= MQTT_KEEPALIVE / 2:
                try:
                    cl.ping()
                except (OSError, mqtt.MqttError) as e:
                    log.info("events: keepalive failed (%s)", e)
                    cl.close(clean=False)
                    self.client = None
                    self._next_try = 0.0
            return
        if time.monotonic() >= self._next_try:
            self._ensure()               # back online, and the state with it

    def _finish(self) -> None:
        cl, self.client = self.client, None
        if cl is None or not cl.connected:
            return
        try:
            cl.publish(self.topic("availability"), b"offline", retain=True)
        except OSError:
            pass
        cl.close(clean=True)


def ha_discovery(cfg: Config) -> list[tuple[str, dict[str, Any]]]:
    """Home Assistant MQTT discovery: a device with two sensors - how
    playback stands (what plays as its attributes) and the title."""
    node = re.sub(r"[^A-Za-z0-9_-]", "_", cfg.client_id or default_client_id())
    state, avail = f"{cfg.prefix}/state", f"{cfg.prefix}/availability"
    device = {"identifiers": [node], "name": "dopeIPTV", "manufacturer": "dopeIPTV",
              "model": "dopeIPTV", "sw_version": _version()}
    common = {"state_topic": state, "availability_topic": avail, "payload_available": "online",
              "payload_not_available": "offline", "device": device}
    return [
        (f"{HA_PREFIX}/sensor/{node}/playback/config",
         dict(common, name="Playback", unique_id=f"{node}_playback", icon="mdi:television-play",
              value_template="{{ value_json.state }}", json_attributes_topic=state)),
        (f"{HA_PREFIX}/sensor/{node}/title/config",
         dict(common, name="Now playing", unique_id=f"{node}_title", icon="mdi:television-classic",
              value_template="{{ value_json.title | default('') }}")),
    ]


# -- the dispatcher ----------------------------------------------------------------

class Dispatcher:
    """Every receiver the settings name; the window tells it what happens."""

    def __init__(self) -> None:
        self.cfg = Config()
        self.sinks: list[_Sink] = []
        self._old: list[_Sink] = []
        self.state = state_payload("idle")
        self.quitting = False

    @property
    def active(self) -> bool:
        return bool(self.sinks) and not self.quitting

    def configure(self, cfg: Config) -> bool:
        """The receivers *cfg* names, started; the ones before told to finish
        (never waited for here). True when something changed."""
        if cfg == self.cfg or self.quitting:
            return False
        for s in self.sinks:
            s.close(1.0)
        self._old = [s for s in self._old + self.sinks if s.alive]
        self.cfg = cfg
        made: list[_Sink] = [WebhookSink(u, cfg.header) for u in cfg.hooks]
        if cfg.mqtt:
            made.append(MqttSink(cfg, after=[s for s in self._old if isinstance(s, MqttSink)]))
        for s in made:
            if isinstance(s, MqttSink):
                s.put("state", self.state)
            s.start()
        self.sinks = made
        if made:
            log.info("events: to %d webhook(s)%s", len(cfg.hooks),
                     f" and MQTT {cfg.mqtt_host}:{cfg.mqtt_port}" if cfg.mqtt else "")
        return True

    def emit(self, event: str, fields: dict[str, Any] | None = None, private: bool = False,
             state: tuple[str, dict[str, Any] | None] | None = None) -> dict[str, Any] | None:
        """Queue *event* for every receiver, and the new *state* (what plays
        now) for the broker. Returns the message, None when nothing is on."""
        if not self.active:
            return None
        now = time.time()
        message = payload(event, fields, private, now)
        for s in self.sinks:
            s.put("event", message)
        if state is not None:
            self.set_state(*state, private=private, now=now)
        return message

    def set_state(self, state: str, fields: dict[str, Any] | None = None, private: bool = False,
                  now: float | None = None) -> None:
        self.state = state_payload(state, fields, private, now)
        for s in self.sinks:
            if isinstance(s, MqttSink):
                s.put("state", self.state)

    def quit(self, drain_s: float = 1.0) -> None:
        """app_quitting and the state "off" sent, every receiver told to end."""
        if self.sinks and not self.quitting:
            self.emit("app_quitting", state=("off", None))
        self.quitting = True
        for s in self.sinks:
            s.close(drain_s)

    def join(self, timeout: float) -> None:
        end = time.monotonic() + timeout
        for s in self.sinks + self._old:
            s.join(end - time.monotonic())


# -- recordings ---------------------------------------------------------------------

class RecordingWatch:
    """The recording jobs' statuses, compared each time they change: which
    started, finished or failed. A stopped one is told once it settled -
    the app looks at a recording's file a moment after it stops, and one
    with nothing in it turns from done to failed then."""

    SETTLE_S = 2.5

    def __init__(self, jobs: Iterable[dict] = ()) -> None:
        self._seen: dict[str, str] = {str(j.get("id")): str(j.get("status")) for j in jobs}
        self._ending: dict[str, float] = {}
        self._reasons: dict[str, str] = {}

    def stopped(self, title: str, reason: str) -> None:
        """RecordingManager.recording_stopped: why it ended."""
        self._reasons[title] = reason

    @property
    def pending(self) -> bool:
        return bool(self._ending)

    @staticmethod
    def _fields(j: dict) -> dict[str, Any]:
        return {"title": j.get("title") or "", "channel": j.get("channel") or ""}

    def update(self, jobs: Iterable[dict], now: float | None = None) -> list[tuple[str, dict[str, Any]]]:
        t = time.monotonic() if now is None else now
        out: list[tuple[str, dict[str, Any]]] = []
        by_id: dict[str, dict] = {}
        for j in jobs:
            jid, status = str(j.get("id")), str(j.get("status"))
            by_id[jid] = j
            before = self._seen.get(jid)
            self._seen[jid] = status
            if status == before:
                continue
            if status == "recording":
                out.append(("recording_started", self._fields(j)))
            elif status == "done" and before == "recording":
                self._ending[jid] = t
            elif status == "failed" and before in ("recording", "done", "scheduled"):
                self._ending.pop(jid, None)
                self._reasons.pop(str(j.get("title") or ""), None)
                out.append(("recording_failed", dict(self._fields(j), error=_safe_text(j.get("error")))))
        for jid, at in list(self._ending.items()):
            job = by_id.get(jid)
            if job is None:
                del self._ending[jid]
            elif t - at >= self.SETTLE_S:
                del self._ending[jid]
                if job.get("status") == "done":
                    title = str(job.get("title") or "")
                    out.append(("recording_finished", dict(
                        self._fields(job), file=os.path.basename(str(job.get("path") or "")),
                        reason=self._reasons.pop(title, "") or "finished")))
        for jid in [k for k in self._seen if k not in by_id]:
            del self._seen[jid]
        return out


# -- the Nerd page's checks ---------------------------------------------------------

def example_event() -> dict[str, Any]:
    return payload("playback_started", {"kind": "live", "title": "TV4 HD", "channel": "TV4 HD",
                                        "programme": "Nyheterna", "source": "Main"})


def _about() -> list[str]:
    return ["", "Events: app_started, app_quitting, playback_started, channel_changed, playback_paused,",
            "  playback_resumed, playback_stopped, playback_failed, recording_started,",
            "  recording_finished, recording_failed (and test, from this button).",
            "While viewing privately: what happened, not what (no title or channel).",
            "", "An event, for example:", json.dumps(example_event(), ensure_ascii=False, indent=2)]


def check_hooks(urls: list[str], header: tuple[str, str] | None,
                timeout: float = HTTP_TIMEOUT) -> Check:
    """A test event POSTed to each address: did it arrive (2xx)?"""
    if not urls:
        return Check(FAIL, "nerd_ck_hooks_none")
    message = payload("test", {"version": _version()})
    lines: list[str] = []
    took: list[int] = []
    denied = 0
    for url in urls:
        t0 = time.monotonic()
        code, err = post(url, message, header, timeout)
        ms = int((time.monotonic() - t0) * 1000)
        if 200 <= code < 300:
            took.append(ms)
            lines.append(f"POST {shown_url(url)}  →  HTTP {code} ({ms} ms)")
        else:
            denied += code in (401, 403)
            lines.append(f"POST {shown_url(url)}  →  " + (f"HTTP {code}" if code else err))
    lines += ["", "Headers sent:", "  Content-Type: application/json; charset=utf-8"]
    if header is not None:
        lines.append(f"  {header[0]}: {MASK}")
    lines += ["", "The test event:", json.dumps(message, ensure_ascii=False, indent=2)] + _about()
    detail = "\n".join(lines)
    if len(took) == len(urls):
        return Check(OK, "nerd_ck_hooks_ok", {"ms": max(took)}, detail)
    if took:
        return Check(WARN, "nerd_ck_hooks_some", {"ok": len(took), "n": len(urls)}, detail)
    if denied:
        return Check(FAIL, "nerd_ck_hooks_denied", {}, detail)
    return Check(FAIL, "nerd_ck_hooks_fail", {}, detail)


def check_mqtt(cfg: Config, wait_s: float = 3.0) -> Check:
    """Connect as the app does (another client id, so the app's own
    connection stays), subscribe to <prefix>/event, publish a test event
    and wait for it to come back: then it arrived."""
    if not cfg.mqtt_host:
        return Check(FAIL, "nerd_ck_mqtt_nohost")
    where = f"{cfg.mqtt_host}:{cfg.mqtt_port}"
    test_id = (cfg.client_id or default_client_id())[:18] + "-test"
    cl = mqtt.Client(cfg.mqtt_host, cfg.mqtt_port, test_id, cfg.mqtt_user, cfg.mqtt_pass,
                     cfg.mqtt_tls, keepalive=30, timeout=MQTT_TIMEOUT)
    lines = [f"TCP {where}{' (TLS)' if cfg.mqtt_tls else ''}",
             f"Client id: {test_id} (the app itself uses {cfg.client_id or default_client_id()})",
             f"Login: {'user name (set)' if cfg.mqtt_user else 'none'}"
             f"{', password (set)' if cfg.mqtt_pass else ''}"]
    topic = f"{cfg.prefix}/event"
    message = payload("test", {"version": _version()})
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")

    def done(state: str, key: str, params: dict[str, Any] | None = None) -> Check:
        topics = ["", "Topics:", f"  {cfg.prefix}/event         each event",
                  f"  {cfg.prefix}/state         what plays now (retained)",
                  f"  {cfg.prefix}/availability  online / offline (retained, the last will)"]
        if cfg.ha:
            topics += [f"  {t}" for t, _c in ha_discovery(cfg)]
        return Check(state, key, params or {}, "\n".join(lines + topics + _about()))
    try:
        secs = cl.connect()
        lines.append(f"CONNECT  →  accepted ({int(secs * 1000)} ms)")
    except mqtt.MqttRefused as e:
        lines.append(f"CONNECT  →  {e}")
        if e.code in (4, 5):
            return done(FAIL, "nerd_ck_mqtt_login")
        return done(FAIL, "nerd_ck_mqtt_refused", {"code": e.code})
    except ssl.SSLError as e:
        lines.append(f"Error: {type(e).__name__}: {e}")
        return done(FAIL, "nerd_ck_mqtt_tls")
    except mqtt.MqttError as e:
        lines.append(f"Error: {e}")
        return done(FAIL, "nerd_ck_mqtt_notmqtt", {"where": where})
    except (OSError, ValueError) as e:
        lines.append(f"Error: {type(e).__name__}: {e}")
        return done(FAIL, "nerd_ck_mqtt_down", {"where": where})
    try:
        granted = cl.subscribe(topic)
        lines.append(f"SUBSCRIBE {topic}  →  " + ("refused" if granted & 0x80 else "granted"))
        t0 = time.monotonic()
        cl.publish(topic, data)
        lines.append(f"PUBLISH {topic}")
        back = not granted & 0x80 and cl.wait_for(topic, data, wait_s)
        ms = int((time.monotonic() - t0) * 1000)
        if not back:
            cl.ping()          # it has read the PUBLISH when it answers this
            lines.append(f"PINGREQ  →  PINGRESP: the broker took it ({ms} ms); it did not come back")
            lines += ["", "The test event:", json.dumps(message, ensure_ascii=False, indent=2)]
            return done(OK, "nerd_ck_mqtt_sent")
        lines.append(f"  →  came back from the broker ({ms} ms)")
        lines += ["", "The test event:", json.dumps(message, ensure_ascii=False, indent=2)]
        return done(OK, "nerd_ck_mqtt_ok", {"ms": ms})
    except (OSError, mqtt.MqttError) as e:
        lines.append(f"Error: {type(e).__name__}: {e}")
        return done(FAIL, "nerd_ck_mqtt_down", {"where": where})
    finally:
        cl.close()
