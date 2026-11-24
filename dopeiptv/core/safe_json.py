"""JSON from outside, nested no deeper than any real answer is.

Python 3.11's json stopped a document nested about a thousand deep with a
RecursionError, and the app's readers were written and fuzzed against what
it let through. From 3.12 json reads one many thousands deep - and a reader
that walks what it got (a Plex tune answer's session, a box's lists) went
over Python's own recursion limit on it. ``deeper_than`` looks at what was
parsed, without recursion and in a bounded number of steps, so every answer
is held to the same depth whatever Python reads (core/_lazy_requests.py:
``Response.json()``). No Qt.
"""
from __future__ import annotations

from typing import Any

#: Deepest a document from outside may nest. A real answer is a few levels
#: deep; this leaves room for any and stays far under Python's recursion
#: limit for a reader that walks it.
MAX_DEPTH = 200


def deeper_than(obj: Any, limit: int = MAX_DEPTH) -> bool:
    """Whether *obj* (what json gave) nests lists and objects more than
    *limit* deep. Iterative; it stops at the first path past the limit."""
    if not isinstance(obj, (dict, list)):
        return False
    stack: list[tuple[Any, int]] = [(obj, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > limit:
            return True
        children = node.values() if isinstance(node, dict) else node
        for child in children:
            if isinstance(child, (dict, list)):
                stack.append((child, depth + 1))
    return False
