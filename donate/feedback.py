"""Error reports and feedback from the app, and the admin's view of them.

The app sends POST /feedback/report - kind, version, system and the text
the person read, changed and sent (the site's footer sends feedback the
same way, marked "from": "site") (core/report.py there scrubs it of
anything that identifies a stream or a person first). Nothing else is
kept: no address, no IP (the rate limit holds it in memory only), no
user agent. The text is scrubbed once more here, in case an older app or
a hand-made request sends something it should not.

Stored one report a line in feedback.jsonl beside the statistics; what an
admin marked (seen, fixed) in feedback-state.json.

Not a way to fill the disk or the inbox: the same report twice in a day
is kept once; past SAME_SIG_DAY reports of one problem in a day the rest
are only counted (no text); past DAY_MOST reports a day, everyone
together, none are taken until the next day; the file never grows past
MAX_FILE. (The app waits a minute between two and stops at a few a day;
nginx and donate.py limit each address as well.)

Answers, without a support desk: the app keeps a random code for a report
the person wants an answer to and sends only its SHA-256 ("reply"). An
admin may answer one report, or a whole group - every report of one
problem, those to come included ("fixed in 1.5.1" reaches all of them) -
with a few fixed answers the app shows in its own language (REPLY_CODES)
and words of their own. The app asks now and then with its codes (POST
/feedback/replies) and is told the answers to the reports they hash to.
Nobody is named anywhere: the code is the only link, and it never leaves
the person's computer. Kept in feedback-replies.json.

Which build sent it: the app's X-dopeIPTV-Build header (official.py) -
"official" or "source"; "unknown" for none (apps from before it, the
site's footer). A modified build is refused before anything is read
(donate.py), so none is kept. Reports from before the header are unknown.
The admin page shows "source" and filters by it, and its Copy takes a
report as plain text (plain()).
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
#: What feedback is about, where, how often: the app's short lists, nothing else kept.
CHOICES = {"topic": ("bug", "idea", "like", "question"),
           "area": ("playback", "guide", "recording", "lists", "settings", "cast", "updates", "other",
                    "website"),
           "often": ("always", "sometimes", "once")}
REPLY_CODES = ("thanks", "known", "fixed", "needinfo", "note")
#: The build kinds a report is kept with (official.py says which came).
BUILDS = ("official", "source", "unknown")
_HASH = re.compile(r"^[0-9a-f]{64}$")
DAY_MOST = 2000
SAME_SIG_DAY = 50
MAX_FILE = 64 << 20
_LOCK = threading.Lock()
# What the limits remember (a day back), read from the file once.
_seen: dict[str, dict] = {}

_URL = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]{1,15})://[^\s'\"<>()\[\]]+")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_EMAIL = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})+")
_MAC = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])")
_KEYVAL = re.compile(r"(?i)\b(user(?:name)?|pass(?:word)?|pwd|token|key|secret|auth|mac)(\s*[=:]\s*)([^\s&'\",;]+)")
_VERSION = re.compile(r"^[0-9A-Za-z.+_-]{1,40}$")
_SUPPORTER = re.compile(r"(?i)\bDS1-[A-Za-z0-9_-]{20,}")


def scrub(text: str) -> str:
    out = _URL.sub(lambda m: f"{m.group(1)}://<address>", text or "")
    out = _SUPPORTER.sub("<supporter key>", out)
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


def _same(kind: str, text: str) -> str:
    return hashlib.sha256((kind + "\0" + " ".join(text.lower().split())).encode("utf-8")).hexdigest()[:24]


def _memory(stats_dir: str, now: float) -> dict:
    """The limits' memory for *stats_dir*: hashes of the last day's
    reports, today's count, today's count per problem. Under _LOCK."""
    today = dt.date.fromtimestamp(now).isoformat()
    mem = _seen.get(stats_dir)
    if mem is None:
        mem = {"hashes": {}, "day": today, "n": 0, "sigs": {}}
        for r in _rows(stats_dir):
            t = r.get("t", 0)
            if now - t >= 86400:
                continue
            if r.get("text"):
                mem["hashes"][_same(r.get("kind", ""), r["text"])] = t
            if dt.date.fromtimestamp(t).isoformat() == today:
                mem["n"] += 1
                sig = r.get("sig") or ""
                mem["sigs"][sig] = mem["sigs"].get(sig, 0) + 1
        _seen[stats_dir] = mem
    if mem["day"] != today:
        mem.update(day=today, n=0, sigs={})
    if len(mem["hashes"]) > 20000 or any(now - t >= 86400 for t in list(mem["hashes"].values())[:1]):
        mem["hashes"] = {h: t for h, t in mem["hashes"].items() if now - t < 86400}
    return mem


def accept(stats_dir: str, body: dict, build: str = "unknown") -> tuple[int, dict]:
    """One report from the app; (status, answer). *build*: what its
    X-dopeIPTV-Build header said (official.kind), never the body's word. A
    report already taken answers ok with "duplicate" (the app need not try
    again), and is not kept twice."""
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
    now = time.time()
    row = {"id": uuid.uuid4().hex[:12], "t": int(now), "kind": kind,
           "version": version, "os": system, "build": build if build in BUILDS else "unknown",
           "sig": signature(kind, text), "text": text}
    if _HASH.match(str(body.get("reply") or "")):
        row["reply"] = str(body["reply"])
    if body.get("supporter") == "yes":
        # From someone whose app holds a supporter key: read first. Only the
        # word comes, never the key, so it names nobody.
        row["sup"] = 1
    if kind == "feedback":
        row.update({k: str(body.get(k)) for k, allowed in CHOICES.items() if body.get(k) in allowed})
        if body.get("from") == "site":
            row["from"] = "site"
    os.makedirs(stats_dir, exist_ok=True)
    path, _state = _paths(stats_dir)
    with _LOCK:
        try:
            if os.path.getsize(path) >= MAX_FILE:
                return 503, {"error": "full"}
        except OSError:
            pass
        mem = _memory(stats_dir, now)
        same = _same(kind, text)
        if same in mem["hashes"]:
            return 200, {"ok": True, "duplicate": True}
        if mem["n"] >= DAY_MOST:
            return 429, {"error": "enough for today"}
        sig = row["sig"]
        if kind != "feedback" and mem["sigs"].get(sig, 0) >= SAME_SIG_DAY:
            # Counted, so the problem's number stays right; its text is
            # already here many times over today.
            row["text"], row["counted"] = "", True
        mem["hashes"][same] = now
        mem["n"] += 1
        mem["sigs"][sig] = mem["sigs"].get(sig, 0) + 1
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return 200, {"ok": True, "id": row["id"]}


#: How long a report is kept: the privacy policy says 12 months.
KEEP_DAYS = 365


def prune(stats_dir: str, now: float | None = None) -> int:
    """Reports older than KEEP_DAYS go, and the answers written to them
    (the file is rewritten whole and swapped in, never half). How many
    went."""
    path, _state = _paths(stats_dir)
    cut = (now if now is not None else time.time()) - KEEP_DAYS * 86400
    with _LOCK:
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return 0
        keep, gone = [], 0
        for line in lines:
            try:
                old = float(json.loads(line).get("t") or 0) < cut
            except (ValueError, AttributeError):
                old = False                      # never drop what cannot be read
            if old:
                gone += 1
            else:
                keep.append(line)
        if not gone:
            return 0
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
        os.replace(tmp, path)
    return gone


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


def _item_state(state: dict, r: dict) -> str:
    """A report's own state, else its problem's (a crash marked seen or fixed
    in Problems is no longer unread), else new."""
    own = state.get(r["id"])
    if own:
        return own
    sig = r.get("sig") or ""
    return (state.get(_sig_key(sig)) if sig else None) or "new"


# -- answers -------------------------------------------------------------------------

def _replies_path(stats_dir: str) -> str:
    return os.path.join(stats_dir, "feedback-replies.json")


def _replies(stats_dir: str) -> dict:
    try:
        with open(_replies_path(stats_dir), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    data.setdefault("report", {})
    data.setdefault("group", {})
    return data


def add_reply(stats_dir: str, target: str, key: str, code: str, text: str = "", version: str = "") -> bool:
    """An answer to one report (*target* "report", its id) or to a group
    ("group", its key): one of REPLY_CODES, words of the admin's own, and
    for "fixed" the version it is fixed in (empty: the next one)."""
    if target not in ("report", "group") or code not in REPLY_CODES or not re.match(r"^[0-9a-f]{8,40}$", key or ""):
        return False
    text = (text or "").strip()[:1500]
    version = version.strip() if _VERSION.match(version.strip() or "-") else ""
    if code == "note" and not text:
        return False
    entry = {"t": int(time.time()), "code": code}
    if text:
        entry["text"] = text
    if version and code == "fixed":
        entry["version"] = version
    with _LOCK:
        data = _replies(stats_dir)
        data[target].setdefault(key, []).append(entry)
        tmp = _replies_path(stats_dir) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, _replies_path(stats_dir))
    if code == "fixed" and target == "group":
        mark(stats_dir, key, "fixed")
    return True


def _answers(row: dict, data: dict) -> list[dict]:
    out = list(data["report"].get(row.get("id", ""), []))
    if row.get("kind") != "feedback" and row.get("sig"):
        out += data["group"].get(_sig_key(row["sig"]), [])
    return sorted(out, key=lambda e: e.get("t", 0))


def replies_for(stats_dir: str, hashes: list) -> dict:
    """{hash: [answers]} for the codes' hashes that have any (the app asks
    with up to 20)."""
    want = {h for h in hashes[:20] if isinstance(h, str) and _HASH.match(h)}
    if not want:
        return {}
    data = _replies(stats_dir)
    out: dict[str, list] = {}
    for r in _asking(stats_dir):
        h = r.get("reply")
        if h in want:
            got = _answers(r, data)
            if got:
                out.setdefault(h, []).extend(got)
    return out


_ASKING: dict = {"key": None, "rows": []}


def _asking(stats_dir: str) -> list[dict]:
    """The reports whose senders can be told answers: kept in memory and read
    again only when the file changes - every copy of the app asks now and
    then, and the file may hold many megabytes."""
    path, _state = _paths(stats_dir)
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return []
    with _LOCK:
        if _ASKING["key"] != key:
            _ASKING["rows"] = [{"id": r.get("id"), "sig": r.get("sig"), "kind": r.get("kind"),
                                "reply": r["reply"]} for r in _rows(stats_dir) if r.get("reply")]
            _ASKING["key"] = key
        return _ASKING["rows"]


def summary(stats_dir: str, days: int) -> dict:
    """What the Feedback page shows: the numbers, a row a day, the problems
    grouped, the latest reports (without their full text)."""
    rows = _rows(stats_dir)
    since = time.time() - days * 86400 if days > 0 else 0
    rows = [r for r in rows if r.get("t", 0) >= since]
    state = _state(stats_dir)
    answered = _replies(stats_dir)
    per_day: dict[str, dict[str, int]] = {}
    by_version: dict[str, int] = {}
    by_os: dict[str, int] = {}
    by_build: dict[str, int] = {}
    kinds = dict.fromkeys(KINDS, 0)
    groups: dict[str, dict] = {}
    for r in rows:
        day = dt.date.fromtimestamp(r["t"]).isoformat()
        d = per_day.setdefault(day, dict.fromkeys(KINDS, 0))
        d[r["kind"]] = d.get(r["kind"], 0) + 1
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        by_version[r["version"]] = by_version.get(r["version"], 0) + 1
        by_os[r["os"]] = by_os.get(r["os"], 0) + 1
        build = _build(r)
        by_build[build] = by_build.get(build, 0) + 1
        if r["kind"] != "feedback":
            key = _sig_key(r.get("sig") or "")
            g = groups.setdefault(key, {"key": key, "sig": r.get("sig") or "", "kind": r["kind"], "count": 0,
                                        "first": r["t"], "last": r["t"], "versions": set(), "systems": set(),
                                        "latest": r["id"], "answerable": 0,
                                        "builds": dict.fromkeys(BUILDS, 0),
                                        "replies": len(answered["group"].get(key, []))})
            g["count"] += 1
            g["builds"][build] += 1
            g["answerable"] += 1 if r.get("reply") else 0
            g["first"] = min(g["first"], r["t"])
            if r["t"] >= g["last"]:
                g["last"] = r["t"]
                if r.get("text"):
                    g["latest"] = r["id"]
            g["versions"].add(r["version"])
            g["systems"].add(r["os"])
    group_list = []
    for g in groups.values():
        g["versions"] = sorted(g["versions"])
        g["systems"] = sorted(g["systems"])
        g["state"] = state.get(g["key"], "new")
        group_list.append(g)
    group_list.sort(key=lambda g: (g["state"] == "fixed", -g["count"], -g["last"]))
    topics: dict[str, int] = {}
    for r in rows:
        if r["kind"] == "feedback":
            topics[r.get("topic") or ""] = topics.get(r.get("topic") or "", 0) + 1
    items = [{"id": r["id"], "t": r["t"], "kind": r["kind"], "version": r["version"], "os": r["os"],
              "build": _build(r), "sig": r.get("sig") or "", "preview": _preview(r), "state": _item_state(state, r),
              "answerable": bool(r.get("reply")), "replies": len(answered["report"].get(r["id"], [])),
              **{k: r[k] for k in (*CHOICES, "from", "sup") if r.get(k)}}
             for r in sorted(rows, key=lambda r: -r["t"]) if r.get("text")][:300]
    open_groups = sum(1 for g in group_list if g["state"] != "fixed")
    return {"total": len(rows), "kinds": kinds, "open_groups": open_groups, "topics": topics,
            "new": sum(1 for i in items if i["state"] == "new"),
            "per_day": [[day, v["error"], v["crash"], v["feedback"]] for day, v in sorted(per_day.items())],
            "by_version": sorted(by_version.items(), key=lambda kv: -kv[1]),
            "by_os": sorted(by_os.items(), key=lambda kv: -kv[1]),
            "by_build": sorted(by_build.items(), key=lambda kv: -kv[1]),
            "groups": group_list[:100], "items": items}


def _build(row: dict) -> str:
    b = row.get("build")
    return b if b in BUILDS else "unknown"


def _preview(row: dict) -> str:
    text = row.get("text") or ""
    if row.get("kind") == "feedback":
        first = text.split("\n\n", 1)[0]
    else:
        first = row.get("sig") or text
    return re.sub(r"\s+", " ", first)[:160]


#: What the app's short lists mean, for the plain text (feedback.js shows the same).
_WORDS = {"topic": {"bug": "not working", "idea": "idea", "like": "likes", "question": "question"},
          "often": {"always": "every time", "sometimes": "sometimes", "once": "once"}}


def plain(row: dict) -> str:
    """A report as plain text, whole, for the admin page's Copy: what it is,
    when, the version, system and build it came from, what the person
    picked, and the text as sent - the error and its traceback, the recent
    log and their own words are in it, as the app wrote them."""
    kind = row.get("kind") if row.get("kind") in KINDS else "feedback"
    t = int(row.get("t") or 0)
    lines = [f"dopeIPTV {kind} report {row.get('id', '')}".rstrip(),
             "Sent: " + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t)),
             f"Kind: {kind}",
             f"Version: {row.get('version') or '?'}",
             f"System: {row.get('os') or '?'}",
             f"Build: {_build(row)}",
             "From: " + ("the website" if row.get("from") == "site" else "the app")]
    if row.get("sup"):
        lines.append("Supporter: yes")
    for key, label in (("topic", "Topic"), ("area", "Area"), ("often", "How often")):
        if row.get(key):
            lines.append(f"{label}: " + _WORDS.get(key, {}).get(row[key], str(row[key])))
    if kind != "feedback" and row.get("sig"):
        lines.append(f"Problem: {row['sig']}")
    text = (row.get("text") or "").strip("\n")
    return "\n".join(lines) + "\n\n" + (text or "(no text kept: the same problem many times that day)") + "\n"


def item(stats_dir: str, rid: str) -> dict | None:
    for r in _rows(stats_dir):
        if r.get("id") == rid:
            out = {k: v for k, v in r.items() if k != "reply"}
            out["build"] = _build(r)
            out["state"] = _state(stats_dir).get(rid, "new")
            out["answerable"] = bool(r.get("reply"))
            out["replies"] = _answers(r, _replies(stats_dir))
            out["copy"] = plain(r)
            return out
    return None


_ID = re.compile(r"^[0-9a-f]{8,40}$")
COPY_MOST = 300


def copies(stats_dir: str, ids: list[str]) -> list[dict]:
    """[{"id", "copy"}] for the reports *ids* names, in that order (the
    admin page's "Copy all shown"; one read of the file for all of them)."""
    want = [i for i in dict.fromkeys(ids) if isinstance(i, str) and _ID.match(i)][:COPY_MOST]
    if not want:
        return []
    wanted = set(want)
    found = {r.get("id"): r for r in _rows(stats_dir) if r.get("id") in wanted}
    return [{"id": i, "copy": plain(found[i])} for i in want if i in found]


def export(stats_dir: str, days: int, most: int = 200) -> dict:
    """For reading elsewhere with the read token (GET /feedback/export): the
    summary, and the latest reports with their whole text."""
    out = summary(stats_dir, days)
    texts = {r["id"]: r.get("text") or "" for r in _rows(stats_dir)}   # the reply hashes stay here
    out["items"] = [dict(i, text=texts.get(i["id"], "")) for i in out["items"][:most]]
    return out
