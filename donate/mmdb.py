"""mmdb.py - the country of an address, from a MaxMind DB file.

The admin page's countries come from DB-IP's free "IP to Country Lite"
database (CC BY 4.0, https://db-ip.com), in the MaxMind DB format, looked
up here on the server - no address is sent anywhere. A reader of that
format in the standard library only, as the rest of donate/, enough for a
country: the search tree and the data section's types.

    db = Reader("/usr/local/www/iptv/stats/dbip-country-lite.mmdb")
    db.country("203.0.113.5")   -> "SE" | ""
"""
from __future__ import annotations

import ipaddress
import struct

_MARKER = b"\xab\xcd\xefMaxMind.com"


class Reader:
    def __init__(self, path: str) -> None:
        with open(path, "rb") as fh:
            self._buf = fh.read()
        at = self._buf.rfind(_MARKER, max(0, len(self._buf) - 128 * 1024))
        if at < 0:
            raise ValueError(f"{path}: not a MaxMind DB file")
        meta_start = at + len(_MARKER)
        self.meta, _ = self._decode(meta_start, meta_start)
        self._nodes = int(self.meta["node_count"])
        self._size = int(self.meta["record_size"])
        if self._size not in (24, 28, 32):
            raise ValueError(f"{path}: record size {self._size}")
        self._tree = self._size * 2 // 8 * self._nodes
        self._data = self._tree + 16
        self._v4start = 0
        if int(self.meta.get("ip_version", 6)) == 6:
            node = 0
            for _ in range(96):
                if node >= self._nodes:
                    break
                node = self._record(node, 0)
            self._v4start = node

    # -- the search tree ---------------------------------------------------------
    def _record(self, node: int, bit: int) -> int:
        b = self._buf
        if self._size == 24:
            o = node * 6 + bit * 3
            return (b[o] << 16) | (b[o + 1] << 8) | b[o + 2]
        if self._size == 28:
            o = node * 7
            if bit == 0:
                return ((b[o + 3] & 0xF0) << 20) | (b[o] << 16) | (b[o + 1] << 8) | b[o + 2]
            return ((b[o + 3] & 0x0F) << 24) | (b[o + 4] << 16) | (b[o + 5] << 8) | b[o + 6]
        o = node * 8 + bit * 4
        return struct.unpack_from(">I", b, o)[0]

    def lookup(self, addr: str):
        ip = ipaddress.ip_address(addr)
        packed = ip.packed
        bits = len(packed) * 8
        node = self._v4start if ip.version == 4 else 0
        if ip.version == 6 and int(self.meta.get("ip_version", 6)) == 4:
            return None
        for i in range(bits):
            if node >= self._nodes:
                break
            node = self._record(node, (packed[i >> 3] >> (7 - (i & 7))) & 1)
        if node <= self._nodes:                 # == node_count: not in the database
            return None
        value, _ = self._decode(self._tree + (node - self._nodes), self._data)
        return value

    def country(self, addr: str) -> str:
        try:
            rec = self.lookup(addr)
        except (ValueError, IndexError, KeyError, struct.error):
            return ""
        if not isinstance(rec, dict):
            return ""
        for key in ("country", "registered_country"):
            iso = (rec.get(key) or {}).get("iso_code") if isinstance(rec.get(key), dict) else None
            if iso:
                return str(iso)
        return ""

    # -- the data section --------------------------------------------------------
    def _decode(self, off: int, base: int, depth: int = 0):
        if depth > 64:
            raise ValueError("mmdb: nested too deep")
        b = self._buf
        ctrl = b[off]
        off += 1
        kind = ctrl >> 5
        if kind == 1:                           # pointer: the value elsewhere
            ss, vvv = (ctrl >> 3) & 3, ctrl & 7
            if ss == 0:
                ptr, off = (vvv << 8) | b[off], off + 1
            elif ss == 1:
                ptr, off = ((vvv << 16) | (b[off] << 8) | b[off + 1]) + 2048, off + 2
            elif ss == 2:
                ptr, off = ((vvv << 24) | (b[off] << 16) | (b[off + 1] << 8) | b[off + 2]) + 526336, off + 3
            else:
                ptr, off = struct.unpack_from(">I", b, off)[0], off + 4
            value, _ = self._decode(base + ptr, base, depth + 1)
            return value, off
        if kind == 0:                           # extended type
            kind = 7 + b[off]
            off += 1
        size = ctrl & 0x1F
        if size == 29:
            size, off = 29 + b[off], off + 1
        elif size == 30:
            size, off = 285 + ((b[off] << 8) | b[off + 1]), off + 2
        elif size == 31:
            size, off = 65821 + ((b[off] << 16) | (b[off + 1] << 8) | b[off + 2]), off + 3
        if kind == 2:                           # UTF-8 string
            return b[off:off + size].decode("utf-8", "replace"), off + size
        if kind == 7:                           # map
            out = {}
            for _ in range(size):
                k, off = self._decode(off, base, depth + 1)
                v, off = self._decode(off, base, depth + 1)
                out[k] = v
            return out, off
        if kind == 11:                          # array
            arr = []
            for _ in range(size):
                v, off = self._decode(off, base, depth + 1)
                arr.append(v)
            return arr, off
        if kind in (5, 6, 9, 10):               # unsigned integers
            return int.from_bytes(b[off:off + size], "big"), off + size
        if kind == 8:                           # int32
            v = int.from_bytes(b[off:off + size], "big")
            if size == 4 and v & 0x80000000:
                v -= 1 << 32
            return v, off + size
        if kind == 3:                           # double
            return struct.unpack_from(">d", b, off)[0], off + 8
        if kind == 15:                          # float
            return struct.unpack_from(">f", b, off)[0], off + 4
        if kind == 4:                           # bytes
            return bytes(b[off:off + size]), off + size
        if kind == 14:                          # boolean: the size is the value
            return bool(size), off
        if kind in (12, 13):                    # data cache container, end marker
            return None, off
        raise ValueError(f"mmdb: unknown type {kind}")
