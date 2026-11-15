"""Finding a service on the home network by multicast DNS: the packets read
(compressed names included, a damaged one refused), and a question answered
by a stand-in responder on this computer."""
import socket
import struct
import threading

import pytest

from dopeiptv.providers import mdns

SERVICE = "_xbmc-jsonrpc-h._tcp.local"


def _name(labels, packet=b"", pointers=None):
    """A name, compressed against the names already in *pointers*."""
    out = b""
    labels = tuple(labels)
    for i in range(len(labels)):
        rest = labels[i:]
        if pointers is not None and rest in pointers:
            return out + struct.pack("!H", 0xC000 | pointers[rest])
        if pointers is not None:
            pointers[rest] = len(packet) + len(out)
        raw = labels[i].encode()
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def _rr(packet, pointers, name, rtype, rdata_fn):
    head = _name(name, packet, pointers)
    start = len(packet) + len(head) + 10
    rdata = rdata_fn(start)
    return head + struct.pack("!HHIH", rtype, 0x8001, 120, len(rdata)) + rdata


def answer(ident=0, instance="Kodi (Living room)", host="kodipi", ip="192.168.1.20",
           port=8080, with_srv=True, question=True, with_a=True):
    """A response the way Avahi gives one: the PTR, and the SRV, TXT and A
    that go with it as additional records, every name compressed."""
    svc = mdns.labels(SERVICE)
    inst = (instance,) + svc
    target = (host, "local")
    ptrs: dict = {}
    pkt = b""
    records = []
    qd = b""
    if question:
        qd = _name(svc, b"\x00" * 12, ptrs) + struct.pack("!HH", mdns.T_PTR, 1)
    pkt = b"\x00" * 12 + qd

    def add(name, rtype, fn):
        nonlocal pkt
        rec = _rr(pkt, ptrs, name, rtype, fn)
        pkt += rec
        records.append(rec)

    add(svc, mdns.T_PTR, lambda at: _name(inst, b"\x00" * at, ptrs))
    if with_srv:
        add(inst, mdns.T_SRV, lambda at: struct.pack("!HHH", 0, 0, port)
            + _name(target, b"\x00" * (at + 6), ptrs))
        add(inst, mdns.T_TXT, lambda at: b"\x09txtvers=1\x08uuid=abc")
        if with_a:
            add(target, mdns.T_A, lambda at: socket.inet_aton(ip))
    head = struct.pack("!HHHHHH", ident, 0x8400, 1 if question else 0, 1,
                       0, len(records) - 1)
    return head + pkt[12:]


def test_a_response_is_read_with_its_compressed_names():
    found = mdns.instances([(answer(), "192.168.1.20")], SERVICE)
    assert len(found) == 1
    inst = found[0]
    assert (inst.name, inst.host, inst.port, inst.target) == (
        "Kodi (Living room)", "192.168.1.20", 8080, "kodipi.local")
    assert inst.txt == {"txtvers": "1", "uuid": "abc"}
    # An instance whose host has no address takes the sender's.
    found = mdns.instances([(answer(with_a=False, question=False), "10.0.0.7")],
                           SERVICE)
    assert found[0].host == "10.0.0.7" and found[0].port == 8080
    # No SRV: not an instance anyone can reach yet.
    assert mdns.instances([(answer(with_srv=False), "1.2.3.4")], SERVICE) == []
    assert mdns._missing_srv([(answer(with_srv=False), "1.2.3.4")], SERVICE) == [
        ("Kodi (Living room)",) + mdns.labels(SERVICE)]


def test_what_is_not_an_answer_is_refused():
    with pytest.raises(ValueError):
        mdns.parse(b"short")
    # A question, not an answer: nothing in it.
    q = mdns.query([(mdns.labels(SERVICE), mdns.T_PTR)], 7)
    assert mdns.parse(q) == []
    assert q[12:].startswith(b"\x0f_xbmc-jsonrpc-h\x04_tcp\x05local\x00")
    # A name that points at itself.
    loop = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0) + b"\xc0\x0c"
    with pytest.raises(ValueError):
        mdns.parse(loop)
    # Cut off half way.
    with pytest.raises(ValueError):
        mdns.parse(answer()[:40])
    assert mdns.instances([(b"\x00" * 5, "x"), (answer()[:50], "y")], SERVICE) == []
    with pytest.raises(ValueError):
        mdns.encode_name(("x" * 64,))


class Responder:
    """A responder on this computer that answers as Kodi's would. With
    *lazy*, the first answer leaves the SRV out, as some do."""

    def __init__(self, lazy=False):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.addr = self.sock.getsockname()
        self.lazy = lazy
        self.questions = []
        self.stop = False
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        self.sock.settimeout(0.2)
        while not self.stop:
            try:
                data, who = self.sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                return
            ident, _f, qd = struct.unpack("!HHH", data[:6])
            name, at = mdns._read_name(data, 12)
            qtype = struct.unpack("!H", data[at:at + 2])[0]
            self.questions.append((name, qtype))
            if qtype == mdns.T_PTR:
                reply = answer(ident, with_srv=not self.lazy, ip="127.0.0.1")
            else:
                reply = answer(ident, ip="127.0.0.1")
            self.sock.sendto(reply, who)

    def close(self):
        self.stop = True
        self.sock.close()


@pytest.mark.parametrize("lazy", [False, True])
def test_a_question_is_answered_back_to_its_own_port(lazy):
    r = Responder(lazy=lazy)
    try:
        found = mdns.browse(SERVICE, timeout=0.6, addr=r.addr)
    finally:
        r.close()
    assert [(i.name, i.host, i.port) for i in found] == [
        ("Kodi (Living room)", "127.0.0.1", 8080)]
    assert r.questions[0] == (mdns.labels(SERVICE), mdns.T_PTR)
    # The one that left the SRV out was asked for it.
    assert any(t == mdns.T_SRV for _n, t in r.questions) == lazy


def test_nothing_answering_is_an_empty_list():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    addr = s.getsockname()
    try:
        assert mdns.browse(SERVICE, timeout=0.3, addr=addr) == []
    finally:
        s.close()
