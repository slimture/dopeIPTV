"""JSON from outside held to one depth whatever Python reads.

Python 3.11's json stopped a document nested about a thousand deep; from
3.12 it reads one many thousands deep, and a reader that walked what it got
(a Plex tune answer) went over the recursion limit - CI, on 3.12, found it.
``Response.json()`` now refuses a document deeper than core/safe_json's
MAX_DEPTH on every Python."""
import json

import pytest

from dopeiptv.core import safe_json
from dopeiptv.providers import plex


def nested(depth: int):
    doc: object = 1
    for _ in range(depth):
        doc = {"a": [doc]}
    return doc


def test_deeper_than_counts_lists_and_objects_without_recursion():
    assert not safe_json.deeper_than(1)
    assert not safe_json.deeper_than({"a": 1})
    assert safe_json.deeper_than(nested(60), 100)            # 120 levels
    assert not safe_json.deeper_than(nested(40), 100)
    assert safe_json.deeper_than(nested(20_000))             # no RecursionError
    wide = {"rows": [{"id": i, "tags": ["x"]} for i in range(100_000)]}
    assert not safe_json.deeper_than(wide)


def test_an_answer_nested_deeper_than_any_is_no_json(monkeypatch):
    from dopeiptv.core import _lazy_requests
    r = _lazy_requests._classes()["Response"]()      # what every answer becomes
    r.status_code = 200
    r._content = ("[" * 5000 + "]" * 5000).encode()
    r.encoding = "utf-8"
    with pytest.raises(ValueError):
        r.json()
    r._content = json.dumps(nested(10)).encode()
    assert r.json() == nested(10)


def test_a_plex_tune_answer_thousands_deep_is_read_without_recursion():
    deep = nested(5000)
    assert plex._find_session(deep) == ""
    found = {"MediaContainer": {"a": [deep, {"key": "/livetv/sessions/abc"}]}}
    assert plex._find_session(found) == "/livetv/sessions/abc"
    # The answer's own order: the first session in it.
    two = {"x": [{"key": "/livetv/sessions/one"}], "y": {"key": "/livetv/sessions/two"}}
    assert plex._find_session(two) == "/livetv/sessions/one"
