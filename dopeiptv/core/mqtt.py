"""A small MQTT 3.1.1 client, in the standard library only: CONNECT (a
login, a last will, keepalive), PUBLISH at QoS 0, retained or not,
SUBSCRIBE (the Nerd page's Test reads its own message back), PINGREQ and
DISCONNECT. That is all the app needs to tell a broker what happens - no
QoS 1/2, no session kept by the broker (clean session), nothing read but
the answers to what it asked.

Plain TCP on 1883, or TLS (8883) through the ssl module, the broker's
certificate checked against the system's and requests' certificates.
Every call waits at most *timeout* seconds; nothing here may run on the
window's thread.
"""

from __future__ import annotations

import socket
import ssl
import time
from typing import Callable

CONNECT, CONNACK, PUBLISH, SUBSCRIBE, SUBACK = 1, 2, 3, 8, 9
PINGREQ, PINGRESP, DISCONNECT = 12, 13, 14

PINGREQ_PACKET = b"\xc0\x00"
DISCONNECT_PACKET = b"\xe0\x00"
MAX_PACKET = 256 * 1024          # what is read back; the app's own are a few hundred bytes

# CONNACK return codes (MQTT 3.1.1, 3.2.2.3).
REFUSED = {1: "unacceptable protocol version", 2: "client id not allowed",
           3: "server unavailable", 4: "bad user name or password", 5: "not authorized"}


class MqttError(Exception):
    """The broker said something that is not MQTT, or broke off."""


class MqttRefused(MqttError):
    """CONNACK with a non-zero return code."""

    def __init__(self, code: int) -> None:
        super().__init__(f"connection refused: {REFUSED.get(code, 'code %d' % code)} ({code})")
        self.code = code


# -- packets ---------------------------------------------------------------------

def encode_length(n: int) -> bytes:
    """The remaining length: 7 bits a byte, the high bit for "more"."""
    if not 0 <= n <= 268_435_455:
        raise ValueError("packet too large")
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _utf8(s: str | bytes) -> bytes:
    raw = s.encode("utf-8") if isinstance(s, str) else s
    if len(raw) > 65535:
        raise ValueError("string too long for MQTT")
    return len(raw).to_bytes(2, "big") + raw


def _packet(first: int, body: bytes) -> bytes:
    return bytes([first]) + encode_length(len(body)) + body


def valid_topic(topic: str) -> bool:
    """A topic one may publish to: not empty, no wildcard, no NUL."""
    return bool(topic) and not any(c in topic for c in "+#\0") and len(topic.encode()) <= 65535


def connect_packet(client_id: str, keepalive: int = 60, username: str = "", password: str = "",
                   will: tuple[str, bytes, bool] | None = None) -> bytes:
    """CONNECT, clean session. *will*: (topic, payload, retain) the broker
    publishes when the connection is lost without a DISCONNECT."""
    flags = 0x02
    payload = _utf8(client_id)
    if will is not None:
        topic, message, retain = will
        flags |= 0x04 | (0x20 if retain else 0)
        payload += _utf8(topic) + _utf8(message)
    if username or password:
        # 3.1.1 allows a password only with a user name: an empty one, then.
        flags |= 0x80
        payload += _utf8(username)
        if password:
            flags |= 0x40
            payload += _utf8(password)
    variable = _utf8("MQTT") + bytes([4, flags]) + max(0, min(65535, keepalive)).to_bytes(2, "big")
    return _packet(CONNECT << 4, variable + payload)


def publish_packet(topic: str, payload: bytes, retain: bool = False) -> bytes:
    """PUBLISH at QoS 0 (no packet id)."""
    if not valid_topic(topic):
        raise ValueError(f"not a topic one can publish to: {topic!r}")
    return _packet((PUBLISH << 4) | (1 if retain else 0), _utf8(topic) + payload)


def subscribe_packet(packet_id: int, topic: str) -> bytes:
    return _packet((SUBSCRIBE << 4) | 0x02, packet_id.to_bytes(2, "big") + _utf8(topic) + b"\x00")


def parse_publish(flags: int, body: bytes) -> tuple[str, bytes, bool]:
    """(topic, payload, retained) of a PUBLISH that came in."""
    if len(body) < 2:
        raise MqttError("short PUBLISH")
    n = int.from_bytes(body[:2], "big")
    topic = body[2:2 + n].decode("utf-8", "replace")
    rest = body[2 + n:]
    if (flags >> 1) & 0x03:          # QoS 1/2 carry a packet id
        rest = rest[2:]
    return topic, rest, bool(flags & 0x01)


# -- the connection ------------------------------------------------------------------

def tls_context() -> ssl.SSLContext:
    """The system's certificates, and requests' (certifi) too: a packaged
    build may not find the system's."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


class Client:
    """One connection to a broker. Not thread-safe: one thread uses it."""

    def __init__(self, host: str, port: int = 1883, client_id: str = "dopeiptv",
                 username: str = "", password: str = "", tls: bool = False,
                 keepalive: int = 60, timeout: float = 5.0,
                 will: tuple[str, bytes, bool] | None = None,
                 context: Callable[[], ssl.SSLContext] | None = None) -> None:
        self.host, self.port, self.client_id = host, int(port), client_id
        self.username, self.password, self.tls = username, password, tls
        self.keepalive, self.timeout, self.will = keepalive, timeout, will
        self._context = context or tls_context
        self._sock: socket.socket | None = None
        self._buf = b""
        self._next_id = 1
        self.last_io = 0.0                 # time.monotonic() of the last packet sent
        self.inbox: list[tuple[str, bytes, bool]] = []

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> float:
        """Connect and log in; returns how long it took (seconds). Raises
        OSError (no broker, TLS), MqttRefused or MqttError."""
        t0 = time.monotonic()
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if self.tls:
                sock = self._context().wrap_socket(sock, server_hostname=self.host)
            self._sock, self._buf = sock, b""
            self._send(connect_packet(self.client_id, self.keepalive, self.username,
                                      self.password, self.will))
            ptype, _flags, body = self._read(time.monotonic() + self.timeout, want=CONNACK)
            if len(body) != 2:
                raise MqttError("CONNACK of the wrong size")
            if body[1] != 0:
                raise MqttRefused(body[1])
        except BaseException:
            self._drop()
            raise
        return time.monotonic() - t0

    def publish(self, topic: str, payload: bytes | str, retain: bool = False) -> None:
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        self._send(publish_packet(topic, data, retain))

    def ping(self) -> float:
        """PINGREQ, and wait for PINGRESP: the broker is there and has read
        everything sent before it. Returns the round trip (seconds)."""
        t0 = time.monotonic()
        self._send(PINGREQ_PACKET)
        self._read(t0 + self.timeout, want=PINGRESP)
        return time.monotonic() - t0

    def subscribe(self, topic: str) -> int:
        """SUBSCRIBE at QoS 0; the granted QoS, or 0x80 when refused."""
        pid = self._next_id
        self._next_id = pid % 65535 + 1
        self._send(subscribe_packet(pid, topic))
        _t, _f, body = self._read(time.monotonic() + self.timeout, want=SUBACK)
        if len(body) < 3 or int.from_bytes(body[:2], "big") != pid:
            raise MqttError("SUBACK for another request")
        return body[2]

    def wait_for(self, topic: str, payload: bytes, timeout: float) -> bool:
        """True once *payload* comes back on *topic* within *timeout*."""
        end = time.monotonic() + timeout
        while True:
            for i, (t, p, _r) in enumerate(self.inbox):
                if t == topic and p == payload:
                    del self.inbox[i]
                    return True
            if time.monotonic() >= end:
                return False
            try:
                self._read(end, want=PUBLISH)
            except TimeoutError:
                return False

    def close(self, clean: bool = True) -> None:
        """DISCONNECT (the broker then keeps the will to itself) and close."""
        if self._sock is not None and clean:
            try:
                self._send(DISCONNECT_PACKET)
            except OSError:
                pass
        self._drop()

    # -- the wire ------------------------------------------------------------------

    def _drop(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _send(self, data: bytes) -> None:
        if self._sock is None:
            raise ConnectionError("not connected")
        try:
            self._sock.settimeout(self.timeout)
            self._sock.sendall(data)
        except OSError:
            self._drop()
            raise
        self.last_io = time.monotonic()

    def _fill(self, n: int, end: float) -> None:
        while len(self._buf) < n:
            left = end - time.monotonic()
            if left <= 0 or self._sock is None:
                raise TimeoutError("no answer from the broker")
            self._sock.settimeout(left)
            try:
                chunk = self._sock.recv(65536)
            except (TimeoutError, socket.timeout):
                raise TimeoutError("no answer from the broker") from None
            except OSError:
                self._drop()
                raise
            if not chunk:
                self._drop()
                raise ConnectionError("the broker closed the connection")
            self._buf += chunk

    def _read_one(self, end: float) -> tuple[int, int, bytes]:
        self._fill(2, end)
        length, mult, i = 0, 1, 1
        while True:
            self._fill(i + 1, end)
            byte = self._buf[i]
            length += (byte & 0x7F) * mult
            i += 1
            if not byte & 0x80:
                break
            mult *= 128
            if i > 4:
                self._drop()
                raise MqttError("not an MQTT answer")
        if length > MAX_PACKET:
            self._drop()
            raise MqttError("answer too large")
        self._fill(i + length, end)
        first = self._buf[0]
        body = self._buf[i:i + length]
        self._buf = self._buf[i + length:]
        return first >> 4, first & 0x0F, body

    def _read(self, end: float, want: int) -> tuple[int, int, bytes]:
        """Packets until one of type *want*; a PUBLISH on the way is kept in
        *inbox*. Anything that is no answer to what was asked ends it."""
        while True:
            try:
                ptype, flags, body = self._read_one(end)
            except TimeoutError:
                if want != PUBLISH:
                    self._drop()
                raise
            if ptype == want and ptype != PUBLISH:
                return ptype, flags, body
            if ptype == PUBLISH:
                self.inbox.append(parse_publish(flags, body))
                del self.inbox[:-20]
                if want == PUBLISH:
                    return ptype, flags, body
                continue
            if ptype in (PINGRESP, SUBACK):
                continue
            self._drop()
            raise MqttError(f"not an MQTT answer (packet type {ptype})")
