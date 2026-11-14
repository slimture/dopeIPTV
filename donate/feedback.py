"""Error reports and feedback from the app, and the admin's view of them.

The app sends POST /feedback/report - kind, version, system and the text
the person read, changed and sent (core/report.py there scrubs it of
anything that identifies a stream or a person first). Nothing else is
kept: no address, no IP (the rate limit holds it in memory only), no
user agent. The text is scrubbed once more here, in case an older app or
a hand-made request sends something it should not.

Stored one report a line in feedback.jsonl beside the statistics; what an
admin marked (seen, fixed) in feedback-state.json.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import threading
import time
import uuid

KINDS = ("error", "crash", "feedback")
MAX_TEXT = 48_000
_LOCK = threading.Lock()

_URL = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]{1,15})://[^\s'\"<>()\[\]]+")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_EMAIL = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+")
_MAC = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])")
_KEYVAL = re.compile(r"(?i)\b(user(?:name)?|pass(?:word)?|pwd|token|key|secret|auth|mac)(\s*[=:]\s*)([^\s&'\",;]+)")
_VERSION = re.compile(r"^[0-9A-Za-z.+_-]{1,40}$")


def scrub(text: str) -> str:
    out = _URL.sub(lambda m: f"{m.group(1)}://<address>", text or "")
    out = _EMAIL.sub("<email>", out)
    out = _MAC.sub("<mac>", out)
    out = _IPV4.sub(lambda m: m.group(0) if m.group(0).startswith("127.") else "<ip>", out)
    return _KEYVAL.sub(lambda m: f"{m.group(1)}{m.group(2)}<hidden>", out)


def _paths(stats_dir: str) -> tuple[str, str]:
    return os.path.join(stats_dir, "feedback.jsonl"), os.path.join(stats_dir, "feedback-state.json")


def signature(kind: str, text: str) -> str:
    """What makes two reports the same problem: the exception line (with
    numbers and addresses set aside) and, for a crash, the first frame."""
    if kind == "feedback":
        return ""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    if kind == "crash":
        head = next((ln for ln in lines if ln.startswith("Fatal Python error")), "Crash")
        frame = next((ln for ln in lines if ln.startswith('File "')), "")
        frame = re.sub(r'File ".*[\\/]', 'File "', frame)
        return f"{head} | {frame}"[:300]
    exc = ""
    for ln in lines:
        if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Warning|Exit|Interrupt)\b", ln):
            exc = ln
    exc = exc or next((ln for ln in lines if ln), "")
    return re.sub(r"\d+", "N", exc)[:300]


def accept(stats_dir: str, body: dict) -> tuple[int, dict]:
    """One report from the app; (status, answer)."""
    kind = str(body.get("kind") or "feedback")
    if kind not in KINDS:
        kind = "feedback"
    version = str(body.get("version") or "")
    if not _VERSION.match(version):
        version = "?"
    system = re.sub(r"[^A-Za-z0-9 ._-]", "", str(body.get("os") or ""))[:24] or "?"
    text = scrub(str(body.get("text") or "")).strip()[:MAX_TEXT]
    if not text:
        return 400, {"error": "empty"}
    row = {"id": uuid.uuid4().hex[:12], "t": int(time.time()), "kind": kind,
           "version": version, "os": system, "sig": signature(kind, text), "text": text}
    os.makedirs(stats_dir, exist_ok=True)
    path, _state = _paths(stats_dir)
    with _LOCK, open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 200, {"ok": True, "id": row["id"]}


def _rows(stats_dir: str) -> list[dict]:
    path, _state = _paths(stats_dir)
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _state(stats_dir: str) -> dict:
    try:
        with open(_paths(stats_dir)[1], encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def mark(stats_dir: str, key: str, state: str) -> bool:
    """*key* (a report's id, or a group's signature hash) seen, fixed or new."""
    if state not in ("new", "seen", "fixed") or not re.match(r"^[0-9a-f]{8,40}$", key or ""):
        return False
    with _LOCK:
        data = _state(stats_dir)
        if state == "new":
            data.pop(key, None)
        else:
            data[key] = state
        tmp = _paths(stats_dir)[1] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, _paths(stats_dir)[1])
    return True


def _sig_key(sig: str) -> str:
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16]


def summary(stats_dir: str, days: int) -> dict:
    """What the Feedback page shows: the numbers, a row a day, the problems
    grouped, the latest reports (without their full text)."""
    rows = _rows(stats_dir)
    since = time.time() - days * 86400 if days > 0 else 0
    rows = [r for r in rows if r.get("t", 0) >= since]
    state = _state(stats_dir)
    per_day: dict[str, dict[str, int]] = {}
    by_version: dict[str, int] = {}
    by_os: dict[str, int] = {}
    kinds = dict.fromkeys(KINDS, 0)
    groups: dict[str, dict] = {}
    for r in rows:
        day = dt.date.fromtimestamp(r["t"]).isoformat()
        d = per_day.setdefault(day, dict.fromkeys(KINDS, 0))
        d[r["kind"]] = d.get(r["kind"], 0) + 1
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        by_version[r["version"]] = by_version.get(r["version"], 0) + 1
        by_os[r["os"]] = by_os.get(r["os"], 0) + 1
        if r["kind"] != "feedback":
            key = _sig_key(r.get("sig") or "")
            g = groups.setdefault(key, {"key": key, "sig": r.get("sig") or "", "kind": r["kind"], "count": 0,
                                        "first": r["t"], "last": r["t"], "versions": set(), "systems": set(),
                                        "latest": r["id"]})
            g["count"] += 1
            g["first"] = min(g["first"], r["t"])
            if r["t"] >= g["last"]:
                g["last"], g["latest"] = r["t"], r["id"]
            g["versions"].add(r["version"])
            g["systems"].add(r["os"])
    group_list = []
    for g in groups.values():
        g["versions"] = sorted(g["versions"])
        g["systems"] = sorted(g["systems"])
        g["state"] = state.get(g["key"], "new")
        group_list.append(g)
    group_list.sort(key=lambda g: (g["state"] == "fixed", -g["count"], -g["last"]))
    items = [{"id": r["id"], "t": r["t"], "kind": r["kind"], "version": r["version"], "os": r["os"],
              "sig": r.get("sig") or "", "preview": _preview(r), "state": state.get(r["id"], "new")}
             for r in sorted(rows, key=lambda r: -r["t"])[:300]]
    open_groups = sum(1 for g in group_list if g["state"] != "fixed")
    return {"total": len(rows), "kinds": kinds, "open_groups": open_groups,
            "new": sum(1 for i in items if i["state"] == "new"),
            "per_day": [[day, v["error"], v["crash"], v["feedback"]] for day, v in sorted(per_day.items())],
            "by_version": sorted(by_version.items(), key=lambda kv: -kv[1]),
            "by_os": sorted(by_os.items(), key=lambda kv: -kv[1]),
            "groups": group_list[:100], "items": items}


def _preview(row: dict) -> str:
    text = row.get("text") or ""
    if row.get("kind") == "feedback":
        first = text.split("\n\n", 1)[0]
    else:
        first = row.get("sig") or text
    return re.sub(r"\s+", " ", first)[:160]


def item(stats_dir: str, rid: str) -> dict | None:
    for r in _rows(stats_dir):
        if r.get("id") == rid:
            out = dict(r)
            out["state"] = _state(stats_dir).get(rid, "new")
            return out
    return None


def export(stats_dir: str, days: int, most: int = 200) -> dict:
    """For reading elsewhere with the read token (GET /feedback/export): the
    summary, and the latest reports with their whole text."""
    out = summary(stats_dir, days)
    texts = {r["id"]: r.get("text") or "" for r in _rows(stats_dir)}
    out["items"] = [dict(i, text=texts.get(i["id"], "")) for i in out["items"][:most]]
    return out
