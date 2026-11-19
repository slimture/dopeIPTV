#!/usr/bin/env python3
"""The server's second cleaning of a report (feedback.scrub): the patterns
the app's core/report.py removes - addresses, IPv4 and IPv6, MAC, e-mail,
a supporter key, name=value logins - removed here too, for an old app or a
hand-made request; what only looks like one (a time, a version, a C++ name)
left as it is.

Plain Python: ``python3 donate/test_feedback_scrub.py`` runs them all
(pytest finds them too).
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import feedback  # noqa: E402


def test_ipv6_addresses_go_in_full_and_shortened():
    for addr in ("2001:db8:85a3:0:0:8a2e:370:7334", "2a02:1406:3:4f::1b", "fe80::1",
                 "::1", "FE80::A00:27FF:FE4E:66A1"):
        out = feedback.scrub(f"connect to {addr} failed")
        assert out == "connect to <ip> failed", (addr, out)
    assert feedback.scrub("[2001:db8::7]:8080 refused") == "[<ip>]:8080 refused"


def test_what_only_looks_like_an_address_stays():
    for text in ("21:05:03 started", "at 1.6.7", "std::thread::join", "QWidget::show",
                 "a time 12:30, a ratio 16:9", "127.0.0.1:8765"):
        assert feedback.scrub(text) == text, text


def test_the_other_patterns_still_go():
    out = feedback.scrub("http://host.example/get.php?username=kalle&password=x 10.0.0.5 "
                         "aa:bb:cc:dd:ee:ff kalle@example.se token=abc DS1-" + "A" * 24)
    assert out == "http://<address> <ip> <mac> <email> token=<hidden> <supporter key>", out


def test_logins_in_quotes_json_headers_and_addresses_without_a_scheme_go():
    cases = {
        'password="my secret" next': 'password="<hidden>" next',
        '{"password": "secret123", "user": "kalle"}': '{"password": "<hidden>", "user": "<hidden>"}',
        "Authorization: Bearer abc.def.ghi": "Authorization: <hidden>",
        "got Bearer eyJhbGciOiJIUzI1NiJ9.x.y today": "got Bearer <hidden> today",
        "Cookie: sid=abc; theme=dark": "Cookie: <hidden>",
        "HTTPConnectionPool(host='iptv.example.com', port=8080)": "HTTPConnectionPool(host='<hidden>', port=8080)",
        "iptv.example.com:8080/live/kalle/pw/1.ts failed": "<address> failed",
    }
    for text, scrubbed in cases.items():
        assert feedback.scrub(text) == scrubbed, (text, feedback.scrub(text))
    for keep in ('File "~/dopeIPTV/dopeiptv/ui/main_window.py", line 42', "lib/python3.12/site-packages/x.py",
                 "dopeIPTV 1.6.7/notes", "localhost:3673"):
        assert feedback.scrub(keep) == keep, keep


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
