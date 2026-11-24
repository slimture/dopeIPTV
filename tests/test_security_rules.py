"""Rules of AGENTS.md's "Security and privacy rules" that read the whole code
base: each fails when a change breaks it somewhere new. An exception is
listed beside its rule with its reason."""
from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "dopeiptv"


def _calls(name: str):
    """(file, line, call) of every call of *name* (a bare name or an
    attribute) in the package."""
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            called = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else "")
            if called == name:
                yield path.relative_to(PKG).as_posix(), node.lineno, node


def _modules() -> list[tuple[str, ast.Module]]:
    """(file, its tree) of every module in the package."""
    out = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        out.append((rel, ast.parse(path.read_text(encoding="utf-8"), rel)))
    return out


def _dotted(f: ast.AST) -> str:
    if isinstance(f, ast.Attribute):
        inner = _dotted(f.value)
        return f"{inner}.{f.attr}" if inner else f.attr
    return f.id if isinstance(f, ast.Name) else ""


def _calls_in(tree: ast.AST, fn: ast.AST | None = None):
    """Every call in *tree*: (its dotted name, the function it is in, the call)."""
    for child in ast.iter_child_nodes(tree):
        inner = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
        if isinstance(child, ast.Call):
            yield _dotted(child.func), inner, child
        yield from _calls_in(child, inner)


# -- a kids profile: a category for adults by the provider's own name too ------------

#: Where a category's name is the provider's own already (server mode's
#: lineup and the share read the client's categories, never a name given
#: in the app).
PROVIDER_NAMES_ONLY = {"services/share.py", "services/home_server.py"}


def test_a_category_for_adults_is_judged_by_the_providers_name_too():
    """A name given in the app ("XXX" -> "Fun") never takes a category out
    of a kids profile's limits: every test of a category's name passes the
    provider's own beside it (core/profiles.adult_category's *more*)."""
    found = []
    for rel, line, call in _calls("adult_category"):
        if rel in PROVIDER_NAMES_ONLY:
            continue
        if len(call.args) < 2:
            found.append(f"{rel}:{line}")
    assert not found, "adult_category without the provider's name: " + ", ".join(found)
    assert any(rel == "ui/mw_lists.py" for rel, _l, _c in _calls("adult_category"))


# -- nothing is unpickled: no pickle in the app at all ----------------------------------

#: What reads a pickle (or its kin, which run code the data names as it is read).
UNPICKLERS = {"pickle", "_pickle", "cPickle", "shelve", "dill", "cloudpickle", "jsonpickle",
              "marshal"}


def test_nothing_in_the_app_reads_a_pickle():
    """A pickle may name any callable and call it as it is read: what the
    app keeps on disk is plain data (the guide's index, providers/epg.py,
    is a header and arrays' bytes). No module imports pickle or its kin -
    not even behind a restricted unpickler."""
    found = []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        rel = path.relative_to(PKG).as_posix()
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == "__import__" and node.args
                  and isinstance(node.args[0], ast.Constant)):
                names = [str(node.args[0].value)]
            found += [f"{rel}:{node.lineno} {n}" for n in names
                      if n.split(".")[0] in UNPICKLERS]
    assert not found, "a pickle reader in the app: " + ", ".join(found)


# -- a certificate is checked: never verify=False, the few reads beside a pin --------

#: Where a TLS connection of the app's own checks no certificate chain, each
#: with its reason (rule 3): the function that does it.
UNCHECKED_TLS = {
    # A certificate read to be shown before it is pinned, or compared with
    # the pin, which is then the trust.
    ("core/cert_pin.py", "fetch"),
    # The pinned server of a source: its fingerprint, after the handshake,
    # is the check (core/net_adapter.py).
    ("core/net_adapter.py", "connect"),
    # The bridge: a pin's read, and an alias's server neither the player
    # nor the recorder would check (a server at home without Nerd's strict
    # check, a source the person let play unchecked) - exactly the trust
    # the recorder had reading the address itself (Alias.trust).
    ("core/net_bridge.py", "_unchecked_context"),
}


def _unchecking(node: ast.AST) -> bool:
    """verify=False, CERT_NONE, an unverified context or check_hostname off."""
    if isinstance(node, ast.keyword):
        return (node.arg == "verify" and isinstance(node.value, ast.Constant)
                and node.value.value is False)
    if isinstance(node, ast.Attribute):
        return node.attr in ("CERT_NONE", "_create_unverified_context")
    if isinstance(node, ast.Constant):
        return node.value == "CERT_NONE"
    if isinstance(node, ast.Assign):
        return (any(isinstance(t, ast.Attribute) and t.attr == "check_hostname" for t in node.targets)
                and isinstance(node.value, ast.Constant) and node.value.value is False)
    return False


def _in_functions(tree: ast.AST, fn: str = ""):
    """Every node of *tree* with the name of the innermost function it is in."""
    for child in ast.iter_child_nodes(tree):
        inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
        yield child, inner
        yield from _in_functions(child, inner)


def test_no_certificate_check_is_switched_off_but_beside_a_pin():
    found = {(rel, fn) for rel, tree in _modules() for node, fn in _in_functions(tree)
             if _unchecking(node)}
    assert found <= UNCHECKED_TLS, f"a certificate unchecked elsewhere: {sorted(found - UNCHECKED_TLS)}"
    assert found == UNCHECKED_TLS, f"an exception no longer needed: {sorted(UNCHECKED_TLS - found)}"


# -- the browser and the file manager: through core/xdg.py alone ---------------------

def test_links_and_folders_are_opened_through_core_xdg_alone():
    """open_url opens https (and server mode's page on this computer) only,
    open_folder a folder: nothing else hands an address to the desktop
    (QDesktopServices.openUrl, webbrowser, xdg-open), and no file address
    is made for open_url (a folder is open_folder's)."""
    found = [f"{rel}:{line}" for rel, line, _c in _calls("openUrl") if rel != "core/xdg.py"]
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        text = path.read_text(encoding="utf-8")
        if rel != "core/xdg.py" and ("import webbrowser" in text or '"xdg-open"' in text):
            found.append(rel)
    for rel, line, call in _calls("open_url"):
        if any(isinstance(n, ast.Attribute) and n.attr == "as_uri" for n in ast.walk(call)):
            found.append(f"{rel}:{line} (a file address)")
    assert not found, "opened around core/xdg.py: " + ", ".join(found)


# -- rule 5: XML from outside, without a DTD of its own -------------------------------

#: What parses XML. Every one reads somebody else's document.
XML_PARSERS = {"fromstring", "XML", "iterparse", "XMLPullParser", "XMLParser", "ParserCreate",
               "parseString", "fromstringlist"}
#: Where one may be called: (module, function) -> the check that runs first
#: in that function ("" for the check itself), and why it is the place.
XML_ALLOWED = {
    ("core/safe_xml.py", "fromstring"): ("refuse_dtd", "the shared check's own parse"),
    ("core/safe_xml.py", "__init__"): ("", "the check itself: expat reading the prolog"),
    ("providers/boxes.py", "parse_xml"): ("refuse_dtd", "a box's answer, its DTD dropped first"),
    ("providers/epg.py", "parse_xmltv"): ("refuse_entities", "a guide, checked as it is parsed"),
    ("services/share.py", "lineup_guide"): ("Prolog", "a cached guide, checked as it streams"),
    ("core/svg_safe.py", "_written_again"): ("", "an SVG written out again from what expat "
                                                 "read; its own EntityDeclHandler refuses an "
                                                 "entity that names another or is long"),
}
#: Where "<!ENTITY" or "<!DOCTYPE" may be looked for in the bytes, and why.
ENTITY_BYTES_OK = {
    "core/safe_xml.py": "drop_dtd finds where a box's DOCTYPE starts; refuse_dtd decides",
    "core/svg_safe.py": "a fast path taken only for a plain ASCII-compatible encoding; "
                        "anything else is read by expat",
}


def test_every_xml_parse_of_outside_data_takes_the_safe_check():
    """Rule 5: XML is parsed only after core/safe_xml.py's check (no DTD of
    its own) - through safe_xml.fromstring, boxes.parse_xml or the places
    listed above, each calling the check in the same function."""
    found = []
    for rel, tree in _modules():
        for name, fn, call in _calls_in(tree):
            if name.rsplit(".", 1)[-1] not in XML_PARSERS or name.startswith("safe_xml."):
                continue
            where = (rel, getattr(fn, "name", ""))
            if where not in XML_ALLOWED:
                found.append(f"{rel}:{call.lineno} {name}() in {where[1] or 'the module'}")
                continue
            guard = XML_ALLOWED[where][0]
            if guard and not any(n.rsplit(".", 1)[-1] == guard for n, _f, _c in _calls_in(fn)):
                found.append(f"{rel}:{call.lineno} {name}() without {guard}() first")
    assert not found, "XML parsed without core/safe_xml.py's check:\n" + "\n".join(found)


def test_a_source_s_id_names_a_file_only_through_file_part():
    """Rule 5: an id comes from backups and sync too; a file's name made of
    one takes core/source_files.file_part, never the id as it came."""
    ids = {"pid", "playlist_id", "source_id", "sid"}
    exts = (".json", ".xml", ".pkl", ".gz", ".m3u", ".db", ".d")
    found = []
    for rel, tree in _modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            parts = node.values
            for i, v in enumerate(parts):
                if not isinstance(v, ast.FormattedValue):
                    continue
                inner = v.value.values[0] if isinstance(v.value, ast.BoolOp) else v.value
                if not (isinstance(inner, ast.Name) and inner.id in ids):
                    continue
                after = parts[i + 1] if i + 1 < len(parts) else None
                if isinstance(after, ast.Constant) and str(after.value).startswith(exts):
                    found.append(f"{rel}:{node.lineno}")
    assert not found, found


def test_no_module_decides_on_entities_from_the_bytes():
    """Rule 5: a look at the bytes for <!ENTITY or <!DOCTYPE misses UTF-16
    and a declared UTF-7 - the check is expat's (core/safe_xml.py)."""
    found = []
    for rel, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (bytes, str)):
                v = node.value if isinstance(node.value, str) else node.value.decode("latin-1")
                if v in ("<!ENTITY", "<!DOCTYPE") and rel not in ENTITY_BYTES_OK:
                    found.append(f"{rel}:{node.lineno}")
    assert not found, found


# -- rule 6: a password is never handed to the log -------------------------------------

#: What a password is called where it is held (a backup's, a source's, a login's).
PASSWORD_NAMES = {"password", "passphrase", "passwd", "pw"}
_LOG_LEVELS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


def test_no_log_line_is_handed_a_password():
    """Rule 6: core/log.py's filter is the net, not the plan - nothing named
    a password (a variable, an attribute, a key looked up) is an argument
    of a log call, the backup's password among them."""
    found = []
    for rel, tree in _modules():
        for name, _fn, call in _calls_in(tree):
            parts = name.split(".")
            if len(parts) < 2 or parts[-1] not in _LOG_LEVELS or parts[-2] not in (
                    "log", "_log", "logger", "logging"):
                continue
            for arg in [*call.args, *(k.value for k in call.keywords)]:
                for n in ast.walk(arg):
                    named = (n.id if isinstance(n, ast.Name) else n.attr
                             if isinstance(n, ast.Attribute) else n.value
                             if isinstance(n, ast.Constant) and isinstance(n.value, str) else "")
                    if named in PASSWORD_NAMES:
                        found.append(f"{rel}:{call.lineno} {named}")
    assert not found, "a password handed to the log: " + ", ".join(found)


# -- words from outside never read as markup (rule 5) ---------------------------------

def _plain_words(node: ast.expr) -> bool:
    """Words a Qt window shows as they are: a literal, the app's own words
    with nothing put in them, or what ui/widgets.plain_text escaped."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Call):
        fn = node.func
        called = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        return called == "plain_text" or (called == "tr" and not node.keywords)
    return False


#: Questions and messages whose words hold only the app's own or the
#: person's: (file, the words as the code writes them) -> why.
OWN_WORDS = {
    ("media/players.py", "tr('status_player_not_found_msg', name=name)"): "mpv or VLC",
    ("media/players.py", "tr('status_player_no_handoff', name=name)"): "mpv or VLC",
    ("ui/mw_profiles.py", "tr('pin_wait', n=max(1, math.ceil(wait)))"): "a number",
    ("ui/mw_profiles.py", "text"): "the wrong PIN's words and the wait, a number",
    ("ui/mw_settings.py", "tr('pl_export_failed', error=exc)"): "the file the person picked",
    ("ui/mw_settings.py", "done"): "backup_done's counts and the file the person picked",
    ("ui/mw_settings.py", "tr('pl_import_done', n=added)"): "a number",
    ("ui/mw_settings.py", "tr('backup_pw_short', n=backup.PASSWORD_MIN)"): "a number",
}


def test_a_question_or_message_shows_outside_words_as_words():
    """A QMessageBox or a QInputDialog has no plain mode: a box's, a
    provider's or a backup's words put in one are escaped first
    (ui/widgets.plain_text) - an <img src="file:///..."> there loaded a
    file from the disk, on Windows a UNC path sent the login's hash."""
    found = []
    for name, owner, at in (("warning", "QMessageBox", 2), ("information", "QMessageBox", 2),
                            ("critical", "QMessageBox", 2), ("question", "QMessageBox", 2),
                            ("getText", "QInputDialog", 2), ("getItem", "QInputDialog", 2),
                            ("getInt", "QInputDialog", 2), ("getDouble", "QInputDialog", 2),
                            ("getMultiLineText", "QInputDialog", 2)):
        for rel, line, call in _calls(name):
            fn = call.func
            if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                    and fn.value.id == owner and len(call.args) > at):
                continue
            words = call.args[at]
            if not _plain_words(words) and (rel, ast.unparse(words)) not in OWN_WORDS:
                found.append(f"{rel}:{line} {ast.unparse(words)}")
    assert not found, "outside words that Qt may read as markup: " + ", ".join(found)


#: Labels that show a provider's, a guide's, a box's or a device's words,
#: by the file that makes them: each is set PlainText there.
PLAIN_LABELS = {
    "ui/mw_build.py": {"count_lbl"},
    "ui/mw_build_detail.py": {"stream_error"},
    "ui/source_page.py": {"title", "subtitle", "crumb_name", "problem_label"},
    "media/rewind_bar.py": {"ts_label"},
    "ui/multiview_cell.py": {"_title", "_pl"},
    "ui/mw_updates.py": {"_update_banner_lbl"},
}


def test_labels_with_outside_words_are_plain_text():
    found = []
    for rel, wanted in PLAIN_LABELS.items():
        tree = ast.parse((PKG / rel).read_text(encoding="utf-8"))
        plain = {n.func.value.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "setTextFormat" and isinstance(n.func.value, ast.Attribute)
                 and n.args and ast.unparse(n.args[0]) == "Qt.TextFormat.PlainText"}
        found += [f"{rel}: {name}" for name in sorted(wanted - plain)]
    assert not found, "not PlainText: " + ", ".join(found)


# -- rule 8: no address, proxy or token header in a program's arguments --------------

#: Where ffmpeg is handed "-headers": only the headers that open nothing
#: (core/net_bridge.split_headers) - the rest the bridge adds.
HEADER_ARGUMENTS = {"providers/airplay_bridge.py", "core/restream.py"}


def test_no_program_is_handed_a_proxy_or_a_secret_header_in_its_arguments():
    """Every account on a Linux computer reads a program's arguments: a
    proxy (its login with it) goes to the app's bridge, a program is given
    the bridge in its environment (net_bridge.hand_over, child_env), and
    "-headers" only where the safe ones are picked out first."""
    found = []
    for rel, tree in _modules():
        words = {n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        for word in ("-http_proxy", "--http-proxy", "--http-proxy="):
            if word in words:
                found.append(f"{rel}: {word}")
        if "-headers" in words:
            calls = {_dotted(c.func).rsplit(".", 1)[-1] for c in ast.walk(tree)
                     if isinstance(c, ast.Call)}
            if rel not in HEADER_ARGUMENTS or "split_headers" not in calls:
                found.append(f"{rel}: -headers")
    assert not found, "in a program's arguments: " + ", ".join(found)


# -- offline mode: nothing out but where its guard lets it (core/offline.py) ------------

#: What opens a connection, looks a name up or sends a packet of its own.
WAYS_OUT = {"create_connection", "getaddrinfo", "gethostbyname", "urlopen", "build_opener",
            "HTTPConnection", "HTTPSConnection", "sendto"}
#: Every module with a way out of its own (beside the one adapter every
#: request takes, core/_lazy_requests.py): the guard it asks itself, or None
#: and why it reaches only this computer or the home network.
OWN_WAYS_OUT = {
    "core/conn_test.py": ("host_refusal", "Test connection: the source's host asked first"),
    "core/dest_guard.py": ("refusal", "a redirect's target looked up only once offline mode "
                                      "let it on; check_peer judges every connection"),
    "core/discord_rpc.py": (None, "Discord's own socket on this computer"),
    "core/events.py": ("refusal", "a webhook through urllib"),
    "core/hdhr.py": (None, "server mode's answer to a discover on the home network"),
    "core/home_net.py": (None, "a UDP socket's route asked of the system: no packet sent"),
    "core/mqtt.py": ("host_refusal", "the broker by its name, then by where it landed"),
    "core/nerd_check.py": ("host_refusal", "a Nerd setting tried by the person"),
    "core/net_bridge.py": ("host_refusal", "every connection a program the app starts makes"),
    "core/net_route.py": (None, "the connection the adapter and the bridge open - each asks "
                                "first and judges where it landed"),
    "core/offline.py": (None, "the guard's own look at a name the person gave"),
    "core/remote_api.py": ("stream_refusal", "an address a device at home asks to play; its "
                                             "own test on this computer"),
    "core/rtsp.py": ("host_refusal", "RTSP OPTIONS to a SAT>IP server"),
    "core/tv_input.py": (None, "lircd's socket on this computer"),
    "providers/cast_bridge.py": (None, "this computer's address on the home network"),
    "providers/cast_devices.py": (None, "a device at home added by its address"),
    "providers/dlna.py": (None, "SSDP and a TV on the home network"),
    "providers/hdhomerun.py": (None, "the tuners' broadcast on the home network"),
    "providers/jellyfin.py": (None, "UDP 7359 discovery on the home network"),
    "providers/lan_scan.py": (None, "Find on my network: the home network's addresses alone"),
    "providers/mdns.py": (None, "mDNS on the home network"),
    "providers/plex.py": (None, "GDM on the home network"),
    "providers/radio_browser.py": ("host_refusal", "radio-browser.info's servers by DNS"),
    "services/home_server.py": (None, "its own port on this computer (port_holder)"),
}


def _offline_calls(tree: ast.AST) -> set[str]:
    return {d.split(".", 1)[1] for d, _f, _c in _calls_in(tree) if d.startswith("offline.")}


def test_every_way_out_of_its_own_asks_offline_mode_or_stays_at_home():
    """Rule 1, offline mode: nothing goes to the internet while it is on.
    The one adapter every request takes asks it; any other way out of the
    app's own is listed here - asking the guard, or at home by design."""
    found: dict[str, set[str]] = {}
    for rel, tree in _modules():
        for name, _fn, _call in _calls_in(tree):
            if name.rsplit(".", 1)[-1] in WAYS_OUT:
                found.setdefault(rel, set()).add(name)
    unlisted = sorted(set(found) - set(OWN_WAYS_OUT))
    assert not unlisted, f"a way out offline mode does not know: {unlisted}"
    trees = dict(_modules())
    for rel, (guard, _why) in OWN_WAYS_OUT.items():
        if guard is not None:
            assert guard in _offline_calls(trees[rel]), f"{rel} does not ask offline.{guard}"


def _function(rel: str, name: str) -> ast.AST:
    tree = ast.parse((PKG / rel).read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def test_the_guard_stands_where_every_request_and_stream_passes():
    """The adapter (every request, a route's too), the connection's own
    check, the bridge's connections, the player's door, a recording and
    server mode's restream each ask offline mode."""
    def asks(rel: str, fn: str, *names: str) -> bool:
        called = {d for d, _f, _c in _calls_in(_function(rel, fn))}
        return all(n in called for n in names)
    assert asks("core/_lazy_requests.py", "send", "offline.on", "offline.refusal")
    assert asks("core/dest_guard.py", "check_peer", "offline.on", "offline.peer_refusal")
    assert asks("core/net_bridge.py", "_open", "self._offline", "offline.peer_refusal")
    assert asks("core/net_bridge.py", "_open_plain", "self._offline", "self._to_proxy")
    assert asks("core/net_bridge.py", "_to_proxy", "offline.peer_refusal")
    assert asks("core/net_bridge.py", "_tunnel", "self._to_proxy")
    assert asks("ui/main_window.py", "_play_may_start", "self._offline_may_play")
    assert asks("ui/mw_offline.py", "_offline_may_play", "offline.stream_refusal")
    assert asks("core/recording.py", "_spawn", "offline.stream_refusal")
    assert asks("core/restream.py", "watch", "offline.stream_refusal")
    # A web page: refused before yt-dlp is looked for or run, mpv's hook
    # handed it, or an external player started; yt-dlp itself asks again.
    assert asks("ui/mw_webstreams.py", "_play_web", "offline.refusal")
    assert asks("core/ytdlp.py", "resolve", "offline.refusal")
    # An external mpv or VLC, whichever way it is started.
    assert asks("media/players.py", "launch_player", "offline.stream_refusal")


#: What starts a program (or opens a link in one).
PROGRAM_STARTS = {"subprocess.Popen", "subprocess.run", "subprocess.call",
                  "subprocess.check_call", "subprocess.check_output", "os.system",
                  "os.startfile", "os.execv", "os.execve", "os.execvp", "os.posix_spawn",
                  "QProcess", "QDesktopServices.openUrl"}
#: Every module that starts a program: the guard it asks itself, or None and
#: why the program reaches nothing offline mode must refuse.
OWN_PROGRAMS = {
    "__init__.py": (None, "git describe in a checkout"),
    "core/_alloc.py": (None, "the app itself again, with another allocator"),
    "core/desktop_entry.py": (None, "the desktop's own tools for the app's menu entry"),
    "core/macupdate.py": (None, "hdiutil and codesign on the update already downloaded"),
    "core/nerd.py": (None, "the system's own network tools, read for the VPN guard"),
    "core/platform_macos.py": (None, "caffeinate"),
    "core/platform_windows.py": (None, "PowerShell for the Start menu's shortcut"),
    "core/rec_extras.py": (None, "the command the person wrote, run after a recording"),
    "core/rec_mp4.py": (None, "ffmpeg on a recording's file"),
    "core/rec_view.py": (None, "ffmpeg and ffprobe on a recording's file"),
    "core/recording.py": ("stream_refusal", "the recorder on a stream"),
    "core/restream.py": ("stream_refusal", "ffmpeg on a channel server mode restreams"),
    "core/selfupdate.py": (None, "the update already downloaded, checked"),
    "core/winupdate.py": (None, "the installer already downloaded, checked"),
    "core/xdg.py": (None, "a link the person clicked, in their own browser or file manager"),
    "core/ytdlp.py": ("refusal", "yt-dlp on a web page; its version and the hasher ask "
                                 "nothing of the network"),
    "media/players.py": ("stream_refusal", "an external mpv or VLC"),
    "providers/airplay_bridge.py": (None, "ffmpeg reads an alias through the bridge, which "
                                          "asks offline mode of every connection"),
    "providers/cast_bridge.py": (None, "ffmpeg reads an alias through the bridge, which asks "
                                       "offline mode of every connection"),
    "providers/cast_feed.py": (None, "its reader reads an alias through the bridge, which "
                                     "asks offline mode of every connection"),
    "providers/epg.py": (None, "the guide's parser on the file already downloaded"),
    "ui/mw_local_library.py": (None, "ffmpeg on a local file for its frame"),
}


def _program_starts(tree: ast.AST) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Name)):
            name = _dotted(node)
            if name in PROGRAM_STARTS or name.rsplit(".", 1)[-1] == "QProcess":
                found.add(name)
    return found


def test_every_program_the_app_starts_asks_offline_mode_or_reaches_nothing_outside():
    """Rule 1, offline mode: a program the app starts on an address (mpv,
    VLC, yt-dlp, ffmpeg) goes to the internet as the app's own requests
    would - each is listed here, asking the guard or why it need not."""
    starts = {rel for rel, tree in _modules() if _program_starts(tree)}
    unlisted = sorted(starts - set(OWN_PROGRAMS))
    assert not unlisted, f"a program offline mode does not know: {unlisted}"
    assert not sorted(set(OWN_PROGRAMS) - starts), "listed, and starts nothing any more"
    trees = dict(_modules())
    for rel, (guard, _why) in OWN_PROGRAMS.items():
        if guard is not None:
            assert guard in _offline_calls(trees[rel]), f"{rel} does not ask offline.{guard}"
    # A route's adapter is the capped one's: the same send.
    text = (PKG / "core/net_adapter.py").read_text(encoding="utf-8")
    assert "capped_adapter()" in text and "def send" not in text
