"""The suite split into parts that run at once - CI's test jobs, one part
each (.github/workflows/ci.yml, DOPEIPTV_SHARD=k/N; conftest.py keeps the
part's tests).

A test file stays whole in one part: its module fixtures run once, and a
file's tests may lean on the order they are written in. Files go to the
part with the least time so far, the longest first, by what each took on a
whole run (shard_times.json, tools/shard_times.py writes it from a junit
file); a file not in it weighs what its tests would at the run's average.
Every part works the same split out of the same files, so each test runs in
exactly one."""
from __future__ import annotations

import json
import os
import re

TIMES = os.path.join(os.path.dirname(__file__), "shard_times.json")
PER_TEST = 0.2  # seconds a test is taken to need when nothing is known


def parse(raw: str) -> tuple[int, int] | None:
    """"k/N" as (k, N), 1 <= k <= N; empty is no split. Anything else is a
    mistake worth stopping for: a part that silently ran nothing - or
    everything - would look green."""
    raw = (raw or "").strip()
    if not raw:
        return None
    m = re.fullmatch(r"(\d{1,3})/(\d{1,3})", raw)
    if not m or not 1 <= int(m[1]) <= int(m[2]):
        raise ValueError(f"DOPEIPTV_SHARD wants k/N with 1 <= k <= N, not {raw!r}")
    return int(m[1]), int(m[2])


def load_times(path: str = TIMES) -> dict[str, float]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {str(k): float(v) for k, v in data.items()
            if isinstance(v, (int, float)) and v >= 0} if isinstance(data, dict) else {}


def assign(counts: dict[str, int], times: dict[str, float], parts: int) -> dict[str, int]:
    """Each file (with its number of tests) to a part, 0-based."""
    known = [f for f in counts if f in times]
    tests_known = sum(counts[f] for f in known)
    per_test = (sum(times[f] for f in known) / tests_known) if tests_known else PER_TEST
    weight = {f: times[f] if f in times else per_test * counts[f] for f in counts}
    load = [0.0] * parts
    owner: dict[str, int] = {}
    for f in sorted(counts, key=lambda f: (-weight[f], f)):
        part = min(range(parts), key=lambda p: (load[p], p))
        owner[f] = part
        load[part] += weight[f]
    return owner


def file_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]
