"""A backup's password (core/backup.py: seal, lock, for_file, locked,
Locked.unlock; the design in docs/SECURITY.md, "A backup's password").

- PBKDF2-HMAC-SHA256 as RFC 7914 gives it, and the file's own vector:
  a fixed password, salt, nonce and plain text give exactly these bytes
  (checked once against pyca/cryptography, an independent implementation);
- a round trip gives back today's backup, byte for byte inside;
- a wrong password and a changed byte - in the data or the header - are one
  error, which never says which, and never holds the password;
- a header out of bounds (rounds, salt, nonce, base64, fields) is refused
  before any work;
- a backup without a password is exactly as it always was, and old ones
  still read;
- nothing of the password reaches the log.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import stat

import pytest
from PyQt6.QtCore import QSettings

from dopeiptv.core import backup

PASSWORD = "correct horse battery staple"
SALT = bytes(range(16))
NONCE = bytes(range(0xA0, 0xAC))
PLAIN = b'{\n  "dopeiptv_backup": 1,\n  "stores": {}\n}'
#: What the app's code makes of the four above in 100 000 rounds - the same
#: bytes pyca/cryptography's ChaCha20Poly1305 gives for this key, nonce and
#: associated data.
KEY_HEX = "49d49c25f597846209f0d92e7770ab64e1c75e94b4ce6c509265ee67175d2a1e"
AAD = ('{"cipher":"chacha20-poly1305","dopeiptv_backup":"encrypted","kdf":"pbkdf2-sha256",'
       '"nonce":"oKGio6Slpqeoqaqr","rounds":100000,"salt":"AAECAwQFBgcICQoLDA0ODw==","v":1}')
DATA = "w88XDsP9hlTePvm6bB2l9Qm431TwyByQu9VvtqEnXPcFUV0DZ7uZvK7UNWkBM+rK0wX4hZawamj4+g=="
FILE = ('{\n  "dopeiptv_backup": "encrypted",\n  "v": 1,\n  "kdf": "pbkdf2-sha256",\n'
        '  "cipher": "chacha20-poly1305",\n  "rounds": 100000,\n'
        '  "salt": "AAECAwQFBgcICQoLDA0ODw==",\n  "nonce": "oKGio6Slpqeoqaqr",\n'
        f'  "data": "{DATA}"\n}}')
#: The fewest rounds a file may say: quick enough for every test here.
FEW = backup.MIN_ROUNDS


def _doc() -> dict:
    return {backup.MAGIC: 1, "app_version": "1.6.9", "created": "2026-11-19T10:00:00+00:00",
            "stores": {"main": {"theme": "nord", "language": "sv",
                                "playlists": json.dumps([{"id": "a", "name": "Å", "kind": "xtream",
                                                          "server": "http://a.invalid",
                                                          "username": "u", "password": "pl-secret"}])},
                       "resume": {"resume_positions_a": json.dumps({"vod:1": {"pos": 600}})}}}


def _locked_file(tmp_path, password: str = PASSWORD, name: str = "b.json") -> str:
    path = str(tmp_path / name)
    backup.write(path, backup.lock(_doc(), password, FEW))
    return path


def _edit(path: str, **fields) -> None:
    data = json.loads(open(path, encoding="utf-8").read())
    for key, value in fields.items():
        if value is None:
            data.pop(key)
        else:
            data[key] = value
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


# -- the vectors ------------------------------------------------------------------

def test_pbkdf2_is_rfc_7914s():
    """RFC 7914, section 11, at a low round count (a 32-byte key is the
    first half of its 64)."""
    assert backup._key("passwd", b"salt", 1).hex() == (
        "55ac046e56e3089fec1691c22544b605f94185216dde0465e68b9d57c20dacbc")
    assert backup._key("Password", b"NaCl", 80000).hex() == (
        "4ddcd8f60b98be21830cee5ef22701f9641a4418d04c0414aeff08876b34ab56")


def test_the_files_own_vector():
    assert backup._key(PASSWORD, SALT, FEW).hex() == KEY_HEX
    doc = backup.seal(PLAIN, PASSWORD, FEW, SALT, NONCE)
    assert doc["data"] == DATA
    assert backup._aad({k: v for k, v in doc.items() if k != "data"}).decode() == AAD
    assert backup.encode(doc).decode() == FILE
    # The plain text is today's backup's bytes, exactly.
    assert backup.encode({backup.MAGIC: 1, "stores": {}}) == PLAIN
    b = backup.locked(json.loads(FILE)).unlock(PASSWORD)
    assert b.full and b.stores == {}


def test_a_password_is_the_same_however_its_letters_were_composed(tmp_path):
    composed, decomposed = "Åsa-lösen", "Åsa-lösen"
    assert composed != decomposed
    path = _locked_file(tmp_path, composed)
    assert backup.read_any(path).unlock(decomposed).stores["main"]["theme"] == "nord"


# -- a round trip -------------------------------------------------------------------

def test_a_locked_backup_opens_to_the_backup_it_was(tmp_path):
    doc = _doc()
    path = _locked_file(tmp_path)
    raw = open(path, "rb").read()
    for secret in (b"pl-secret", b"nord", PASSWORD.encode()):
        assert secret not in raw
    got = backup.read_any(path)
    assert isinstance(got, backup.Locked) and got.rounds == FEW
    assert repr(got).startswith("Locked(") and "nord" not in repr(got)
    b = got.unlock(PASSWORD)
    want = backup.parse(json.loads(backup.encode(doc)))
    assert (b.stores, b.sources, b.app_version, b.created) == (
        want.stores, want.sources, want.app_version, want.created)
    if os.name == "posix":                         # as a plain one: the owner's only
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_every_lock_has_its_own_salt_and_nonce():
    a, b = backup.lock(_doc(), PASSWORD, FEW), backup.lock(_doc(), PASSWORD, FEW)
    assert a["salt"] != b["salt"] and a["nonce"] != b["nonce"] and a["data"] != b["data"]
    assert len(base64.b64decode(a["salt"])) == 16 and len(base64.b64decode(a["nonce"])) == 12
    assert backup.lock(_doc(), PASSWORD)["rounds"] == backup.ROUNDS == 600_000


def test_read_says_a_locked_backup_is_locked(tmp_path):
    path = _locked_file(tmp_path)
    with pytest.raises(backup.BackupError) as err:
        backup.read(path)
    assert err.value.reason == "locked"


def test_a_staged_restore_of_a_locked_backup_waits_as_today_and_goes(tmp_path):
    """Opened, it is staged as any backup is: the plain copy beside the
    settings, the owner's only, removed once it is in place."""
    b = backup.read_any(_locked_file(tmp_path)).unlock(PASSWORD)
    pending = str(tmp_path / backup.PENDING_NAME)
    backup.stage(b, pending)
    assert backup.read(pending).stores == b.stores
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(pending).st_mode) == 0o600
    dst = {name: QSettings(str(tmp_path / f"dst-{name}.ini"), QSettings.Format.IniFormat)
           for name in backup.STORES}
    assert backup.apply_pending(dst, pending, lambda _p: True) is True
    assert not os.path.exists(pending)
    assert dst["main"].value("theme") == "nord"


# -- refused ------------------------------------------------------------------------

def test_a_wrong_password_is_one_error_that_says_nothing_more(tmp_path):
    got = backup.read_any(_locked_file(tmp_path))
    with pytest.raises(backup.BackupError) as err:
        got.unlock("correct horse battery stapler")
    assert err.value.reason == "password" and err.value.detail == ""
    assert "stapler" not in str(err.value) and err.value.__cause__ is None


@pytest.mark.parametrize("where", ["first", "middle", "tag"])
def test_a_changed_byte_in_the_data_is_the_same_error(tmp_path, where):
    path = _locked_file(tmp_path)
    data = bytearray(base64.b64decode(json.load(open(path, encoding="utf-8"))["data"]))
    data[{"first": 0, "middle": len(data) // 2, "tag": -1}[where]] ^= 0x01
    _edit(path, data=base64.b64encode(bytes(data)).decode())
    with pytest.raises(backup.BackupError) as err:
        backup.read_any(path).unlock(PASSWORD)
    assert err.value.reason == "password"


@pytest.mark.parametrize("field,value", [
    ("rounds", FEW + 1),
    ("salt", base64.b64encode(bytes(range(1, 17))).decode()),
    ("nonce", base64.b64encode(bytes(12)).decode()),
])
def test_a_changed_header_is_the_same_error(tmp_path, field, value):
    """The header is the tag's associated data: changed within its bounds,
    the tag fails as for a changed byte."""
    path = _locked_file(tmp_path)
    _edit(path, **{field: value})
    with pytest.raises(backup.BackupError) as err:
        backup.read_any(path).unlock(PASSWORD)
    assert err.value.reason == "password"


def test_the_data_cut_short_is_the_same_error(tmp_path):
    path = _locked_file(tmp_path)
    data = base64.b64decode(json.load(open(path, encoding="utf-8"))["data"])
    _edit(path, data=base64.b64encode(data[:-20]).decode())
    with pytest.raises(backup.BackupError) as err:
        backup.read_any(path).unlock(PASSWORD)
    assert err.value.reason == "password"


@pytest.mark.parametrize("rounds", [FEW - 1, backup.MAX_ROUNDS + 1, 0, -600_000, True,
                                    600_000.0, "600000", None, 10 ** 30])
def test_rounds_out_of_range_are_refused_before_any_work(tmp_path, monkeypatch, rounds):
    path = _locked_file(tmp_path)
    _edit(path, rounds=rounds if rounds is not None else [])
    calls = []
    monkeypatch.setattr(backup, "_key", lambda *a: calls.append(a) or b"\0" * 32)
    with pytest.raises(backup.BackupError) as err:
        backup.read_any(path)
    assert err.value.reason == "invalid" and not calls


@pytest.mark.parametrize("fields", [
    {"salt": "!!!!AAECAwQFBgcICQoLDA0ODw=="},                 # not the alphabet
    {"salt": "AAECAwQFBgcICQoLDA0ODw"},                       # padding missing
    {"salt": "AAECAwQFBgcICQoLDA0ODw==\n"},                   # a line break
    {"salt": "AAECAwQFBgcICQoLDA0ODx=="},                     # not the canonical spelling
    {"salt": base64.b64encode(bytes(15)).decode()},           # 15 bytes
    {"salt": base64.b64encode(bytes(17)).decode()},
    {"salt": 1234},
    {"nonce": base64.b64encode(bytes(11)).decode()},
    {"nonce": base64.b64encode(bytes(13)).decode()},
    {"nonce": "oKGio6Slpqeoqaqé"},                       # not ASCII
    {"data": "abc"},                                          # bad padding
    {"data": base64.b64encode(bytes(16)).decode()},           # a tag and nothing
    {"data": None},
    {"v": 0}, {"v": "1"}, {"v": True},
    {"kdf": "scrypt"}, {"cipher": "aes-256-gcm"},
    {"extra": "x"},                                           # a field it does not know
    {"rounds": None},                                         # a field gone
])
def test_a_header_out_of_bounds_is_refused_as_damaged(tmp_path, monkeypatch, fields):
    path = _locked_file(tmp_path)
    _edit(path, **fields)
    monkeypatch.setattr(backup, "_key", lambda *a: pytest.fail("work before the header held"))
    with pytest.raises(backup.BackupError) as err:
        backup.read_any(path)
    assert err.value.reason == "invalid"


def test_a_later_layout_is_said_to_be_from_a_newer_app(tmp_path):
    path = _locked_file(tmp_path)
    _edit(path, v=backup.LOCK_V + 1)
    with pytest.raises(backup.BackupError) as err:
        backup.read_any(path)
    assert err.value.reason == "newer"


@pytest.mark.parametrize("cut", [1, 40, 0.5])
def test_a_truncated_file_is_refused(tmp_path, cut):
    path = _locked_file(tmp_path)
    raw = open(path, "rb").read()
    keep = int(len(raw) * cut) if isinstance(cut, float) else cut
    with open(path, "wb") as fh:
        fh.write(raw[:keep])
    with pytest.raises(backup.BackupError) as err:
        backup.read_any(path)
    assert err.value.reason == "invalid"


def test_a_file_past_the_size_limit_is_refused_unread(tmp_path, monkeypatch):
    path = _locked_file(tmp_path)
    monkeypatch.setattr(backup, "MAX_BYTES", os.path.getsize(path) - 1)
    with pytest.raises(backup.BackupError) as err:
        backup.read_any(path)
    assert (err.value.reason, err.value.detail) == ("invalid", "too large")


def test_deep_nesting_is_refused_not_raised(tmp_path):
    path = tmp_path / "deep.json"
    path.write_text("[" * 200_000 + "]" * 200_000, encoding="utf-8")
    with pytest.raises(backup.BackupError) as err:
        backup.read_any(str(path))
    assert err.value.reason == "invalid"


def test_what_a_password_opened_must_be_a_backup_too():
    for inner in (b"not json", b'{"something": "else"}',
                  backup.encode(backup.seal(PLAIN, PASSWORD, FEW))):
        sealed = backup.seal(inner, PASSWORD, FEW)
        with pytest.raises(backup.BackupError) as err:
            backup.locked(sealed).unlock(PASSWORD)
        assert err.value.reason == "invalid"
    # The parser's words never quote what was opened.
    with pytest.raises(backup.BackupError) as err:
        backup.locked(backup.seal(b"\xffsecret", PASSWORD, FEW)).unlock(PASSWORD)
    assert "secret" not in str(err.value) and "ff" not in err.value.detail


def test_seal_refuses_what_it_cannot_lock():
    for bad in ({"password": ""}, {"rounds": FEW - 1}, {"rounds": True},
                {"salt": bytes(15)}, {"nonce": bytes(13)}):
        kwargs = {"password": PASSWORD, "rounds": FEW, **bad}
        with pytest.raises(ValueError):
            backup.seal(PLAIN, **kwargs)


# -- without a password: as it always was -------------------------------------------

def test_no_password_is_todays_file_byte_for_byte(tmp_path):
    doc = _doc()
    assert backup.for_file(doc, "") is doc
    plain, today = str(tmp_path / "plain.json"), str(tmp_path / "today.json")
    backup.write(plain, backup.for_file(doc))
    with open(today, "wb") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))
    assert open(plain, "rb").read() == open(today, "rb").read()
    got = backup.read_any(plain)
    assert isinstance(got, backup.Backup) and got.stores == backup.read(plain).stores
    # With one, the plain text inside is that same file.
    locked = backup.for_file(doc, PASSWORD)
    assert backup.is_locked(locked) and not backup.is_locked(doc)


def test_an_old_plain_backup_and_an_old_export_still_read(tmp_path):
    old = tmp_path / "old.json"
    old.write_text(json.dumps({backup.MAGIC: 1, "app_version": "1.5.0", "created": "",
                               "stores": {"main": {"theme": "oled"}}}), encoding="utf-8-sig")
    assert backup.read_any(str(old)).stores["main"] == {"theme": "oled"}
    assert backup.read(str(old)).full
    export = tmp_path / "playlists.json"
    export.write_text(json.dumps([{"name": "B", "kind": "m3u", "server": "http://b.invalid"}]),
                      encoding="utf-8")
    assert backup.read_any(str(export)).kind == "playlists"


# -- the log ------------------------------------------------------------------------

class _Said(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def said():
    """Every line the app's logger takes, at every level."""
    from dopeiptv.core.log import log
    handler, level = _Said(), log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        yield handler.lines
    finally:
        log.removeHandler(handler)
        log.setLevel(level)


def test_the_password_and_the_key_never_reach_the_log(tmp_path, said):
    from dopeiptv.core.log import recent_lines
    password = "s3cret-backup-pw"
    path = str(tmp_path / "b.json")
    backup.write(path, backup.for_file(_doc(), password))
    got = backup.read_any(path)
    key = backup._key(password, got.salt, got.rounds)
    got.unlock(password)
    with pytest.raises(backup.BackupError) as err:
        got.unlock(password + "x")
    backup.stage(got.unlock(password), str(tmp_path / backup.PENDING_NAME))
    assert said                                      # the log was listened to
    shown = "\n".join(said + recent_lines(500)) + str(err.value) + repr(got)
    assert password not in shown and key.hex() not in shown
