"""CI's split of the suite (tests/shard.py, conftest.py, ci.yml)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import shard

ROOT = Path(__file__).resolve().parent.parent


def test_every_file_runs_in_exactly_one_part_and_the_parts_are_even():
    counts = {f"tests/test_{i:03d}.py": 1 + i % 7 for i in range(60)}
    times = {f"tests/test_{i:03d}.py": float(1 + (i * 37) % 23) for i in range(0, 60, 2)}
    owner = shard.assign(counts, times, 4)
    assert set(owner) == set(counts) and set(owner.values()) == {0, 1, 2, 3}
    assert owner == shard.assign(dict(reversed(list(counts.items()))), times, 4), "the same split everywhere"
    per_test = sum(times.values()) / sum(counts[f] for f in times)
    load = [0.0] * 4
    for f, part in owner.items():
        load[part] += times.get(f, per_test * counts[f])
    assert max(load) - min(load) <= max(times.values()), load


def test_a_slow_file_has_a_part_of_its_own():
    counts = {"tests/a.py": 1, "tests/b.py": 1, "tests/c.py": 1, "tests/d.py": 1}
    owner = shard.assign(counts, {"tests/a.py": 100.0, "tests/b.py": 1.0,
                                  "tests/c.py": 1.0, "tests/d.py": 1.0}, 2)
    assert owner["tests/a.py"] != owner["tests/b.py"] == owner["tests/c.py"] == owner["tests/d.py"]


@pytest.mark.parametrize("raw", ["0/4", "5/4", "1/0", "x", "1-4", "1/4/2", "-1/4"])
def test_a_wrong_part_stops_the_run(raw):
    with pytest.raises(ValueError):
        shard.parse(raw)


def test_parse_and_no_split():
    assert shard.parse("") is None and shard.parse("  ") is None
    assert shard.parse("2/4") == (2, 4) and shard.parse(" 4/4 ") == (4, 4)
    assert shard.file_of("tests/test_x.py::test_y[a::b]") == "tests/test_x.py"


def test_the_times_name_real_files_and_ci_runs_every_part():
    times = shard.load_times()
    assert times, "tests/shard_times.json is read"
    missing = [f for f in times if not (ROOT / f).is_file()]
    assert len(missing) <= len(times) // 10, f"shard_times.json is stale: {missing[:5]}"
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    parts = re.search(r"shard:\s*\[([^\]]*)\]", ci)
    assert parts, "ci.yml's test jobs have a shard matrix"
    numbers = [int(x) for x in parts[1].split(",")]
    total = re.search(r"DOPEIPTV_SHARD:\s*\$\{\{\s*matrix\.shard\s*\}\}/(\d+)", ci)
    assert total and numbers == list(range(1, int(total[1]) + 1)), "every part 1..N runs"
