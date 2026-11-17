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

A reply to an answer, still anonymous: an answer to one report may let the
person reply ("open", the admin's switch - on by default for "needinfo",
never for a group). The app then sends a follow-up to /feedback/report with
"thread" = the report's code hash. It is taken only while the report's
latest answer lets them reply and they have not replied to it yet - one
question, one answer at a time - else 409 "closed" and nothing is kept. A
follow-up is a row of its own in feedback.jsonl with "thread_of" (the
report's id) and "to" (the answer's id), never the hash: the report keeps
that. It goes when its report goes (prune, delete). The app is told whether
the latest answer lets them reply (replies_for's "open"); it keeps its own
words, so they are never sent back. A follow-up is unread until the admin
opens its report, and takes an archived report out of the archive.

Archived (the admin's choice, in feedback-state.json beside the marks):
out of the lists and the unread count, under Archived. Deleted: a report
for good, with its answers, follow-ups and code hash - or every report of a
problem - the file rewritten whole and swapped in, as prune does.

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
#: A follow-up is the person's words only, kept short (the app's box takes as many).
THREAD_TEXT = 2000
#: What feedback is about, where, how often: the app's short lists, nothing else kept.
CHOICES = {"topic": ("bug", "idea", "like", "question"),
           "area": ("playback", "guide", "recording", "lists", "settings", "cast", "updates", "other",
                    "website"),
           "often": ("always", "sometimes", "once")}
REPLY_CODES = ("thanks", "known", "fixed", "needinfo", "note")
#: The build kinds a report is kept with (official.py says which came).
BUILDS = ("official", "source", "unknown")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[0-9a-f]{8,40}$")
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


def _same(kind: str, text: str, thread_of: str = "") -> str:
    """Two reports the same: the same words - a follow-up only within its
    own thread (a "thanks" there is not the same as one sent before)."""
    lead = kind + "\0" + (thread_of + "\0" if thread_of else "")
    return hashlib.sha256((lead + " ".join(text.lower().split())).encode("utf-8")).hexdigest()[:24]


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
                mem["hashes"][_same(r.get("kind", ""), r["text"], str(r.get("thread_of") or ""))] = t
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
    again), and is not kept twice.

    With "thread" (a report's code hash) it is a follow-up to that report's
    latest answer: taken only while that answer lets them reply and they
    have not yet (409 "closed" otherwise, nothing kept); kept as a row of
    its own, tied to the report by its id, never by the hash."""
    thread = str(body.get("thread") or "")
    if thread and not _HASH.match(thread):
        return 400, {"error": "thread"}
    kind = "feedback" if thread else str(body.get("kind") or "feedback")
    if kind not in KINDS:
        kind = "feedback"
    version = str(body.get("version") or "")
    if not _VERSION.match(version):
        version = "?"
    system = re.sub(r"[^A-Za-z0-9 ._-]", "", str(body.get("os") or ""))[:24] or "?"
    text = scrub(str(body.get("text") or "")).strip()[:THREAD_TEXT if thread else MAX_TEXT]
    if not text:
        return 400, {"error": "empty"}
    now = time.time()
    row = {"id": uuid.uuid4().hex[:12], "t": int(now), "kind": kind,
           "version": version, "os": system, "build": build if build in BUILDS else "unknown",
           "sig": "" if thread else signature(kind, text), "text": text}
    if not thread and _HASH.match(str(body.get("reply") or "")):
        row["reply"] = str(body["reply"])
    if body.get("supporter") == "yes":
        # From someone whose app holds a supporter key: read first. Only the
        # word comes, never the key, so it names nobody.
        row["sup"] = 1
    if kind == "feedback" and not thread:
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
        rid = to = None
        if thread:
            rid, to = _thread_of(stats_dir, thread)
            if rid is None:
                return 409, {"error": "closed"}
        same = _same(kind, text, rid or "")
        if same in mem["hashes"]:
            return 200, {"ok": True, "duplicate": True}
        if thread:
            if to is None:
                return 409, {"error": "closed"}
            row.update(thread_of=rid, to=to)
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
    if rid:
        # A reply waits to be read: out of the archive, unread (its own id
        # has no mark yet).
        archive(stats_dir, rid, False)
    return 200, {"ok": True, "id": row["id"]}


#: How long a report is kept: the privacy policy says 12 months.
KEEP_DAYS = 365


def _write(path: str, write) -> None:
    """*path* written whole beside itself and swapped in, never half."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        write(fh)
    os.replace(tmp, path)


def _write_json(path: str, data: dict) -> None:
    _write(path, lambda fh: json.dump(data, fh, ensure_ascii=False))


def _t(row: dict) -> float:
    try:
        return float(row.get("t") or 0)
    except (TypeError, ValueError):
        return float("inf")                      # never dropped for its age: it cannot be read


def _remove(stats_dir: str, goes, groups: tuple[str, ...] = ()) -> int:
    """The reports *goes(row)* names taken out of feedback.jsonl, each with
    its follow-ups, the answers written to it and its marks - and for
    *groups* (problems' keys) their answers and marks too. Every file is
    rewritten whole and swapped in. How many reports went."""
    path, state_path = _paths(stats_dir)
    with _LOCK:
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            lines = []
        # (line, row, whether it goes): a line that cannot be read is never dropped.
        parsed: list[tuple[str, dict | None, bool]] = []
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                r = None
            r = r if isinstance(r, dict) else None
            parsed.append((line, r, bool(r) and not r.get("thread_of") and bool(goes(r))))
        gone = {str(r.get("id")) for _line, r, g in parsed if g and r}
        kept = {str(r.get("id")) for _line, r, g in parsed if r and not g and not r.get("thread_of")}
        keep, dropped = [], set()
        for line, r, g in parsed:
            # A follow-up goes with its report - and one whose report is no
            # longer there goes too: never a thread without its start.
            if g or (r is not None and r.get("thread_of") and str(r["thread_of"]) not in kept):
                dropped.add(str(r.get("id")) if r else "")
            else:
                keep.append(line)
        if dropped:
            _write(path, lambda fh: fh.writelines(keep))
            _ASKING["key"] = None                # read again, whatever the clock says
        if gone or groups:
            data = _replies(stats_dir)
            changed = False
            for rid in gone:
                changed = data["report"].pop(rid, None) is not None or changed
            for key in groups:
                changed = data["group"].pop(key, None) is not None or changed
            if changed:
                _write_json(_replies_path(stats_dir), data)
        marks = dropped | set(groups)
        state = _state(stats_dir)
        arch = _archived(state)
        if marks & (set(state) | set(arch)):
            for key in marks:
                state.pop(key, None)
                arch.pop(key, None)
            state["archived"] = arch
            _write_json(state_path, state)
    return len(gone)


def prune(stats_dir: str, now: float | None = None) -> int:
    """Reports older than KEEP_DAYS go, with their follow-ups (a thread goes
    with its start, never kept past it), the answers written to them and
    their marks (each file rewritten whole and swapped in, never half). How
    many reports went."""
    cut = (now if now is not None else time.time()) - KEEP_DAYS * 86400
    return _remove(stats_dir, lambda r: _t(r) < cut)


#: The most keys one request acts on at once (the page lists at most 300
#: reports and 100 problems).
MANY = 300


def _keys(key) -> list[str] | None:
    """One key or a list of them, each a report's id, a follow-up's or a
    problem's key; None when one of them is not."""
    keys = [key] if isinstance(key, str) else list(key)[:MANY] if isinstance(key, (list, tuple)) else []
    if not keys or not all(isinstance(k, str) and _ID.match(k) for k in keys):
        return None
    return keys


def delete(stats_dir: str, key: str, group: bool = False) -> int:
    """For good: the report *key* with its thread - its answers, follow-ups
    and code hash, so the app that sent it finds nothing - or with *group*
    every report of the problem *key* and the problem's own answers. How
    many reports went; -1 for a key that is not one."""
    return delete_many(stats_dir, [], [key]) if group else delete_many(stats_dir, [key], [])


def delete_many(stats_dir: str, keys, groups) -> int:
    """For good, in one pass: the reports *keys*, each with its thread, and
    the problems *groups*, each with all its reports and its own answers -
    as delete() does one. How many reports went; -1 when nothing is asked
    or one key is not one (and then nothing goes)."""
    ks = _keys(keys) if keys else []
    gs = _keys(groups) if groups else []
    if ks is None or gs is None or not (ks or gs):
        return -1
    ids, sigs = set(ks), set(gs)
    return _remove(stats_dir, lambda r: str(r.get("id") or "") in ids
                   or (bool(sigs) and bool(r.get("sig")) and _sig_key(str(r["sig"])) in sigs), tuple(gs))


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


def _archived(state: dict) -> dict:
    """What the admin archived ({key: when}), in the state file beside the marks."""
    got = state.get("archived")
    return dict(got) if isinstance(got, dict) else {}


MARKS = ("new", "seen", "fixed")


def mark(stats_dir: str, key, state: str, threads: bool = False) -> bool:
    """*key* - a report's id, a follow-up's, or a group's signature hash; or
    a list of them - seen, fixed or new; with *threads* each report's
    replies too (several marked at once from the list: none left unread)."""
    keys = _keys(key)
    if state not in MARKS or keys is None:
        return False
    if threads:
        asked = set(keys)
        keys += [str(r["id"]) for r in _rows(stats_dir) if isinstance(r, dict) and r.get("thread_of")
                 and str(r["thread_of"]) in asked and _ID.match(str(r.get("id") or ""))]
    with _LOCK:
        data = _state(stats_dir)
        for k in keys:
            if state == "new":
                data.pop(k, None)
            else:
                data[k] = state
        _write_json(_paths(stats_dir)[1], data)
    return True


def archive(stats_dir: str, key, on: bool) -> bool:
    """A report (its id) or a problem (its key) - or a list of them - into
    the archive, out of the lists and the unread count, or back out of it."""
    keys = _keys(key)
    if keys is None:
        return False
    with _LOCK:
        data = _state(stats_dir)
        arch = _archived(data)
        now = int(time.time())
        changed = False
        for k in keys:
            if on:
                arch[k] = now
                changed = True
            elif arch.pop(k, None) is not None:
                changed = True
        if not changed:
            return True                          # none archived: nothing to write
        data["archived"] = arch
        _write_json(_paths(stats_dir)[1], data)
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


def _is_archived(arch: dict, r: dict) -> bool:
    """Archived itself, or through its problem."""
    return r.get("id") in arch or (bool(r.get("sig")) and _sig_key(str(r["sig"])) in arch)


def revision(stats_dir: str) -> str:
    """A word that changes whenever anything the Feedback tab shows does - a
    report or a reply in, an answer, a mark: the admin page asks with it now
    and then and is told "same" without a report being read."""
    parts = []
    for path in (*_paths(stats_dir), _replies_path(stats_dir)):
        try:
            st = os.stat(path)
            parts.append(f"{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append("-")
    return hashlib.sha1("|".join(parts).encode("ascii")).hexdigest()[:16]


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


def add_reply(stats_dir: str, target: str, key: str, code: str, text: str = "", version: str = "",
              let_reply: bool | None = None) -> bool:
    """An answer to one report (*target* "report", its id) or to a group
    ("group", its key): one of REPLY_CODES, words of the admin's own, and
    for "fixed" the version it is fixed in (empty: the next one).
    *let_reply*: whether the person may reply to it - one report's answer
    only, never a group's; None: as its code has it ("needinfo" does)."""
    if target not in ("report", "group") or code not in REPLY_CODES or not _ID.match(key or ""):
        return False
    text = (text or "").strip()[:1500]
    version = version.strip() if _VERSION.match(version.strip() or "-") else ""
    if code == "note" and not text:
        return False
    entry: dict = {"id": uuid.uuid4().hex[:10], "t": int(time.time()), "code": code}
    if text:
        entry["text"] = text
    if version and code == "fixed":
        entry["version"] = version
    if target == "report" and (code == "needinfo" if let_reply is None else bool(let_reply)):
        entry["open"] = True
    with _LOCK:
        data = _replies(stats_dir)
        data[target].setdefault(key, []).append(entry)
        _write_json(_replies_path(stats_dir), data)
    if code == "fixed" and target == "group":
        mark(stats_dir, key, "fixed")
    return True


def _answers(row: dict, data: dict) -> list[dict]:
    """A report's answers, oldest first: its own and, for an error or a
    crash, its problem's (marked "group")."""
    out = [dict(a) for a in data["report"].get(row.get("id", ""), []) if isinstance(a, dict)]
    if row.get("kind") != "feedback" and row.get("sig"):
        out += [dict(a, group=True) for a in data["group"].get(_sig_key(row["sig"]), []) if isinstance(a, dict)]
    return sorted(out, key=lambda e: e.get("t", 0))


def _open_to(answers: list[dict], follows: list[dict]) -> str | None:
    """The answer the person may reply to now: the latest, while it lets
    them (never a group's) and they have not replied to it yet."""
    if not answers:
        return None
    last = answers[-1]
    aid = str(last.get("id") or "")
    if not last.get("open") or last.get("group") or not aid:
        return None
    return None if any(f.get("to") == aid for f in follows) else aid


def _follows(rows: list[dict]) -> dict[str, list[dict]]:
    """{report id: its follow-ups, oldest first}."""
    out: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("thread_of"):
            out.setdefault(str(r["thread_of"]), []).append(r)
    for v in out.values():
        v.sort(key=lambda f: f.get("t", 0))
    return out


def _for_app(a: dict, to: str | None) -> dict:
    """An answer as the app is told it: nothing of the admin's bookkeeping."""
    out = {k: a[k] for k in ("t", "code", "text", "version") if k in a}
    if to and a.get("id") == to:
        out["open"] = True
    return out


def _thread_of(stats_dir: str, h: str) -> tuple[str | None, str | None]:
    """Under _LOCK: the id of the report whose code hashes to *h* (None for
    none), and the answer its sender may reply to now (None: they may not)."""
    rows = _asking_rows(stats_dir)
    reports = [r for r in rows if r.get("reply") == h]
    if not reports:
        return None, None
    data = _replies(stats_dir)
    follows = _follows(rows)
    for r in reports:
        to = _open_to(_answers(r, data), follows.get(str(r["id"]), []))
        if to:
            return str(r["id"]), to
    return str(reports[0]["id"]), None


def replies_for(stats_dir: str, hashes: list) -> dict:
    """{hash: [answers]} for the codes' hashes that have any (the app asks
    with up to 20); the latest says "open" while they may reply to it."""
    want = {h for h in hashes[:20] if isinstance(h, str) and _HASH.match(h)}
    if not want:
        return {}
    data = _replies(stats_dir)
    rows = _asking(stats_dir)
    follows = _follows(rows)
    out: dict[str, list] = {}
    for r in rows:
        h = r.get("reply")
        if h in want:
            got = _answers(r, data)
            if got:
                to = _open_to(got, follows.get(str(r.get("id")), []))
                out.setdefault(h, []).extend(_for_app(a, to) for a in got)
    return out


_ASKING: dict = {"key": None, "rows": []}


def _asking_rows(stats_dir: str) -> list[dict]:
    """Under _LOCK: the reports whose senders can be told answers, and the
    follow-ups to them - kept in memory and read again only when the file
    changes (every copy of the app asks now and then, and the file may hold
    many megabytes); a rewrite (prune, delete) forgets them at once."""
    path, _state = _paths(stats_dir)
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return []
    if _ASKING["key"] != key:
        rows = []
        for r in _rows(stats_dir):
            if not isinstance(r, dict):
                continue
            if r.get("thread_of"):
                rows.append({"id": r.get("id"), "thread_of": r["thread_of"], "to": r.get("to"), "t": r.get("t", 0)})
            elif r.get("reply"):
                rows.append({"id": r.get("id"), "sig": r.get("sig"), "kind": r.get("kind"), "reply": r["reply"]})
        _ASKING["rows"] = rows
        _ASKING["key"] = key
    return _ASKING["rows"]


def _asking(stats_dir: str) -> list[dict]:
    with _LOCK:
        return _asking_rows(stats_dir)


def summary(stats_dir: str, days: int) -> dict:
    """What the Feedback page shows: the numbers, a row a day, the problems
    grouped, the latest reports (without their full text) - a report with a
    reply in the period among them, the most recent activity first."""
    rev = revision(stats_dir)
    every = [r for r in _rows(stats_dir) if isinstance(r, dict)]
    follows = _follows(every)
    reports = [r for r in every if not r.get("thread_of")]
    since = time.time() - days * 86400 if days > 0 else 0
    rows = [r for r in reports if r.get("t", 0) >= since]
    state = _state(stats_dir)
    arch = _archived(state)
    answered = _replies(stats_dir)
    per_day: dict[str, dict[str, int]] = {}
    by_version: dict[str, int] = {}
    by_os: dict[str, int] = {}
    by_build: dict[str, int] = {}
    kinds = dict.fromkeys(KINDS, 0)
    groups: dict[str, dict] = {}
    everywhere: dict[str, int] = {}                  # a problem's reports kept, whatever the period
    for r in reports:
        if r.get("kind") != "feedback":
            key = _sig_key(r.get("sig") or "")
            everywhere[key] = everywhere.get(key, 0) + 1
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
        g["archived"] = g["key"] in arch
        g["all"] = everywhere.get(g["key"], g["count"])
        group_list.append(g)
    group_list.sort(key=lambda g: (g["archived"], g["state"] == "fixed", -g["count"], -g["last"]))
    topics: dict[str, int] = {}
    for r in rows:
        if r["kind"] == "feedback" and not _is_archived(arch, r):
            topics[r.get("topic") or ""] = topics.get(r.get("topic") or "", 0) + 1
    items = []
    for r in reports:
        if not r.get("text"):
            continue
        fl = follows.get(str(r.get("id")), [])
        last = max([r.get("t", 0)] + [f.get("t", 0) for f in fl])
        if last < since:
            continue
        answers = _answers(r, answered)
        latest = answers[-1].get("id") if answers else None
        unread = any(not state.get(str(f.get("id"))) for f in fl)
        items.append({"id": r["id"], "t": r["t"], "kind": r["kind"], "version": r["version"], "os": r["os"],
                      "build": _build(r), "sig": r.get("sig") or "", "preview": _preview(r),
                      "state": "new" if unread else _item_state(state, r),
                      "answerable": bool(r.get("reply")), "replies": len(answered["report"].get(r["id"], [])),
                      "thread": len(fl), "last": last, "archived": _is_archived(arch, r),
                      "open": bool(_open_to(answers, fl)),
                      # They replied to the latest answer: the admin's turn.
                      "waiting": bool(fl and latest and fl[-1].get("to") == latest),
                      **{k: r[k] for k in (*CHOICES, "from", "sup") if r.get(k)}})
    items = sorted(items, key=lambda i: -i["last"])[:300]
    open_groups = sum(1 for g in group_list if g["state"] != "fixed" and not g["archived"])
    return {"rev": rev, "total": len(rows), "kinds": kinds, "open_groups": open_groups, "topics": topics,
            "new": sum(1 for i in items if i["state"] == "new" and not i["archived"]),
            "archived": sum(1 for i in items if i["archived"] and i["kind"] == "feedback"),
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
#: An answer's fixed part as the admin page says it (feedback.js SAID).
_SAID = {"thanks": "Thanks - read and noted.", "known": "A known problem, being worked on.",
         "fixed": "Fixed in {v}.", "needinfo": "More details would help.", "note": ""}


def _utc(t) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(t or 0)))
    except (TypeError, ValueError, OverflowError):
        return "?"


def said(a: dict) -> str:
    """An answer in words: its fixed part, then the admin's own."""
    fixed = _SAID.get(str(a.get("code") or ""), "").replace("{v}", str(a.get("version") or "the next version"))
    return " ".join(p for p in (fixed, str(a.get("text") or "").strip()) if p)


def _conversation(answers: list[dict], follows: list[dict]) -> list[dict]:
    """The answers and follow-ups in the order they came, each follow-up
    right after the answer it replies to ("who": "answer" or "reply")."""
    at = {str(a["id"]): (a.get("t", 0), i) for i, a in enumerate(answers) if a.get("id")}
    keyed = [((a.get("t", 0), i, 0, 0), dict(a, who="answer")) for i, a in enumerate(answers)]
    for f in follows:
        base = at.get(str(f.get("to") or ""))
        order = (base[0], base[1], 1, f.get("t", 0)) if base else (f.get("t", 0), len(answers), 1, f.get("t", 0))
        keyed.append((order, dict(f, who="reply")))
    return [e for _order, e in sorted(keyed, key=lambda kv: kv[0])]


def _plain_entry(e: dict) -> str:
    if e["who"] == "reply":
        return (f"Reply, {_utc(e.get('t'))} (dopeIPTV {e.get('version') or '?'}, {e.get('os') or '?'}):\n"
                + (str(e.get("text") or "").strip("\n") or "(empty)") + "\n")
    head = "Answer to everyone with this problem" if e.get("group") else "Answer"
    note = " (replies allowed)" if e.get("open") else ""
    return f"{head}, {_utc(e.get('t'))}{note}:\n" + (said(e) or "(no words)") + "\n"


def plain(row: dict, answers: list[dict] | None = None, follows: list[dict] | None = None) -> str:
    """A report as plain text, whole, for the admin page's Copy: what it is,
    when, the version, system and build it came from, what the person
    picked, and the text as sent - the error and its traceback, the recent
    log and their own words are in it, as the app wrote them. Then the
    conversation that followed, in order: each answer and each reply, with
    its time."""
    kind = row.get("kind") if row.get("kind") in KINDS else "feedback"
    t = int(row.get("t") or 0)
    lines = [f"dopeIPTV {kind} report {row.get('id', '')}".rstrip(),
             "Sent: " + _utc(t),
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
    out = "\n".join(lines) + "\n\n" + (text or "(no text kept: the same problem many times that day)") + "\n"
    for e in _conversation(list(answers or []), list(follows or [])):
        out += "\n" + _plain_entry(e)
    return out


def plain_reply(f: dict, row: dict, answers: list[dict] | None = None) -> str:
    """A follow-up alone, for its own Copy: whose report it is, when, from
    which version, the answer it replies to, and the person's words."""
    kind = row.get("kind") if row.get("kind") in KINDS else "feedback"
    lines = [f"dopeIPTV reply {f.get('id', '')} to {kind} report {row.get('id', '')}",
             "Sent: " + _utc(f.get("t")),
             f"Version: {f.get('version') or '?'}",
             f"System: {f.get('os') or '?'}",
             f"Build: {_build(f)}"]
    out = "\n".join(lines) + "\n"
    to = next((a for a in answers or [] if a.get("id") and a.get("id") == f.get("to")), None)
    if to is not None:
        quote = "\n".join("> " + ln for ln in (said(to) or "(no words)").splitlines())
        out += f"\nIn reply to the answer of {_utc(to.get('t'))}:\n{quote}\n"
    return out + "\n" + (str(f.get("text") or "").strip("\n") or "(empty)") + "\n"


def _shown(a: dict, to: str | None) -> dict:
    """An answer as the admin page shows it: "open" as it was sent, "can"
    while they may still reply to it."""
    out = {k: a[k] for k in ("t", "code", "text", "version", "open", "group") if k in a}
    out["can"] = bool(to) and a.get("id") == to
    return out


def item(stats_dir: str, rid: str) -> dict | None:
    """One report in full with its conversation: every answer and reply in
    order ("conversation"), which replies are unread, whether they may
    reply now - and its plain text (Copy), each reply's too."""
    rows = [r for r in _rows(stats_dir) if isinstance(r, dict)]
    r = next((x for x in rows if x.get("id") == rid and not x.get("thread_of")), None)
    if r is None:
        return None
    fl = _follows(rows).get(rid, [])
    data = _replies(stats_dir)
    state = _state(stats_dir)
    answers = _answers(r, data)
    to = _open_to(answers, fl)

    def reply(f: dict) -> dict:
        return {"who": "reply", "id": f.get("id"), "t": f.get("t", 0), "text": f.get("text") or "",
                "version": f.get("version") or "?", "os": f.get("os") or "?", "build": _build(f),
                "state": state.get(str(f.get("id"))) or "new", "copy": plain_reply(f, r, answers)}
    out = {k: v for k, v in r.items() if k != "reply"}
    out["build"] = _build(r)
    out["state"] = state.get(rid, "new")
    out["archived"] = rid in _archived(state)
    out["answerable"] = bool(r.get("reply"))
    out["replies"] = [_shown(a, to) for a in answers]
    out["conversation"] = [reply(e) if e["who"] == "reply" else dict(_shown(e, to), who="answer")
                           for e in _conversation(answers, fl)]
    out["unread"] = [str(f.get("id")) for f in fl if not state.get(str(f.get("id")))]
    out["open"] = bool(to)
    out["copy"] = plain(r, answers, fl)
    return out


COPY_MOST = 300


def copies(stats_dir: str, ids: list[str]) -> list[dict]:
    """[{"id", "copy"}] for the reports *ids* names, in that order (the
    admin page's "Copy all shown"; one read of the file for all of them) -
    each with its conversation; a follow-up's id gives that reply alone."""
    want = [i for i in dict.fromkeys(ids) if isinstance(i, str) and _ID.match(i)][:COPY_MOST]
    if not want:
        return []
    rows = [r for r in _rows(stats_dir) if isinstance(r, dict)]
    wanted = set(want)
    found = {r.get("id"): r for r in rows if r.get("id") in wanted}
    heads = {r.get("thread_of") for r in found.values() if r.get("thread_of")}
    reports = {r.get("id"): r for r in rows
               if not r.get("thread_of") and (r.get("id") in wanted or r.get("id") in heads)}
    follows = _follows(rows)
    data = _replies(stats_dir)
    out = []
    for i in want:
        r = found.get(i)
        if r is None:
            continue
        if r.get("thread_of"):
            head = reports.get(r["thread_of"])
            if head is not None:
                out.append({"id": i, "copy": plain_reply(r, head, _answers(head, data))})
            continue
        out.append({"id": i, "copy": plain(r, _answers(r, data), follows.get(i, []))})
    return out


def export(stats_dir: str, days: int, most: int = 200) -> dict:
    """For reading elsewhere with the read token (GET /feedback/export): the
    summary, and the latest reports with their whole text and the replies
    their senders wrote."""
    out = summary(stats_dir, days)
    rows = [r for r in _rows(stats_dir) if isinstance(r, dict)]
    # The reply hashes stay here.
    texts = {r["id"]: r.get("text") or "" for r in rows if r.get("id") and not r.get("thread_of")}
    follows = _follows(rows)
    out["items"] = [dict(i, text=texts.get(i["id"], ""),
                         **({"follow_ups": [{"t": f.get("t", 0), "text": f.get("text") or ""}
                                            for f in follows[i["id"]]]} if follows.get(i["id"]) else {}))
                    for i in out["items"][:most]]
    return out
