"""Is an MPEG transport stream encrypted? Read from its first packets'
transport_scrambling_control bits (ISO/IEC 13818-1, 2.4.3.2).

A box that cannot decrypt a channel (no card, no CI module, no rights)
still sends it: the tables (PAT, PMT, SDT...) in the clear, the picture and
the sound scrambled. A player then shows nothing and says only that loading
failed. Counted over the packets that carry a payload on an elementary
stream's PID - not the null packets, not the tables on the reserved PIDs
(which are never scrambled)."""

from __future__ import annotations

PACKET = 188
SYNC = 0x47
NULL_PID = 0x1FFF
# PIDs 0x0000-0x001F carry the tables (PAT, CAT, NIT, SDT, EIT, TDT...).
_LAST_TABLE_PID = 0x1F
# Fewer packets than this say nothing either way.
MIN_PACKETS = 20
# The share of payload packets scrambled from which the channel is: a
# scrambled picture alone is most of a stream, and a channel the box can
# decrypt has none.
SCRAMBLED_SHARE = 0.25


def sync_offset(data: bytes) -> int:
    """Where the packets begin: the first 0x47 followed by two more a
    packet apart. -1 when *data* is no transport stream."""
    limit = min(len(data) - 2 * PACKET, PACKET)
    for i in range(max(0, limit)):
        if data[i] == SYNC and data[i + PACKET] == SYNC and data[i + 2 * PACKET] == SYNC:
            return i
    return -1


def count(data: bytes) -> tuple[int, int]:
    """(payload packets on elementary-stream PIDs, how many of them are
    scrambled) in *data*. Stops at the first packet out of sync."""
    start = sync_offset(data)
    if start < 0:
        return 0, 0
    payload = scrambled = 0
    for i in range(start, len(data) - PACKET + 1, PACKET):
        if data[i] != SYNC:
            break
        b1, b2, b3 = data[i + 1], data[i + 2], data[i + 3]
        if b1 & 0x80:                       # transport_error_indicator
            continue
        pid = ((b1 & 0x1F) << 8) | b2
        if pid <= _LAST_TABLE_PID or pid == NULL_PID:
            continue
        if not b3 & 0x10:                   # adaptation field only
            continue
        payload += 1
        if b3 & 0xC0:
            scrambled += 1
    return payload, scrambled


def verdict(data: bytes) -> bool | None:
    """True: the stream is encrypted. False: it is in the clear. None: too
    little of a transport stream to say."""
    payload, scrambled = count(data)
    if payload < MIN_PACKETS:
        return None
    return scrambled / payload >= SCRAMBLED_SHARE
