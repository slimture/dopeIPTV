"""How long each test file took, for CI's split of the suite (tests/shard.py).

    QT_QPA_PLATFORM=offscreen python3 -m pytest -q --junitxml=run.xml
    python3 tools/shard_times.py run.xml [more.xml ...]

writes tests/shard_times.json: every file's seconds - its tests' setup,
call and teardown, as junit has them - rounded to a tenth. Run it again
when the parts drift apart (each CI test job prints its time); a file it
does not name still runs, weighed at the average."""
from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "shard_times.json")


def file_of(classname: str) -> str | None:
    parts = classname.split(".")
    for i in range(len(parts), 0, -1):
        rel = "/".join(parts[:i]) + ".py"
        if os.path.isfile(os.path.join(ROOT, rel)):
            return rel
    return None


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 2
    seconds: dict[str, float] = {}
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            rel = file_of(case.get("classname", ""))
            if rel:
                seconds[rel] = seconds.get(rel, 0.0) + float(case.get("time") or 0)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({k: round(v, 1) for k, v in sorted(seconds.items())}, f, indent=0)
        f.write("\n")
    print(f"{len(seconds)} files, {sum(seconds.values()):.0f} s -> {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
