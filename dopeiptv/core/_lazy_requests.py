"""A lazy stand-in for the ``requests`` module, with a ceiling on what an
answer may be.

Importing ``requests`` pulls in urllib3 + charset_normalizer + certifi - about
110 ms of the app's cold start - yet every use in this codebase is inside a
function, on the first actual network call (which happens after the window is
already up, often on a worker thread). So instead of ``import requests`` at
module top - which drags all of that onto the startup import chain - modules do
``from ..core._lazy_requests import requests`` and keep their ``requests.get``
/ ``requests.Session`` / ``except requests.RequestException`` call sites exactly
as before. The real module is imported once, on first attribute access.

Type checkers see the real ``requests`` module (the TYPE_CHECKING branch), so
static analysis of the call sites is unchanged.

**The ceiling.** ``requests`` reads an answer whole into memory unless the
caller streams it, and a server decides how long its answer is: a playlist
or a list that never ended was read until the memory was gone. Every
session and call made through this stand-in (and core/net_adapter.py's
sessions) goes out through an adapter that counts what is read of an answer
the caller did not ask to stream - and of a redirect's own body - and ends
the read past ``RESPONSE_MAX`` (a session's own ``max_response`` where it
says one: a source's lists get ``LIST_MAX``). The read then fails with
``ResponseTooLarge``, a ``requests.RequestException``, so it is handled as
any failed request is. An answer the caller streams (a live channel, a
download to disk, a guide read with its own ceiling) is never counted. And
``Response.json()`` says a document nested deeper than Python reads is not
JSON (a ``ValueError``, as for any other), instead of a RecursionError no
caller expects.

**What it is written in.** An answer that names no charset is read as
JSON's own (UTF-8, or the UTF-16/-32 its first bytes say) by ``json()``,
and its ``text`` guessed from its first ``GUESS_BYTES`` - requests guessed
from all of it, and the guess takes time with the square of some bytes
(minutes for a megabyte). A charset whose decoder does too (punycode:
``safe_xml.slow_codec``) is read as UTF-8.

**What goes to dopeiptv.org** (core/site_out.py): every request through
the adapter is asked ``site_out.check`` first, and one for dopeiptv.org
that holds a login of a source is not sent (``LoginHeld``, a
RequestException).

**Where it lands** (core/dest_guard.py): every connection is looked at once
it is made (``check_peer``), every redirect before it is followed
(``redirect_refusal``, ``RedirectRefused``), and a request that goes
through a proxy - whose connection is the proxy's - has its name looked up
here first (``proxied_refusal``, ``DestinationRefused``: a ConnectionError).

**Offline mode** (core/offline.py): while it is on, a request - a
redirect's hop too - goes only to this computer or the home network, and
through a proxy only one there: anything else is not sent
(``OfflineRefused``, a ConnectionError every caller handles as a network
that is not there). Its connection is judged again on its socket
(dest_guard.check_peer).
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

#: The most an answer read whole may be: well above any API's (a few MB),
#: low enough that a few at once on the pool cannot take the memory.
RESPONSE_MAX = 32 * 1024 * 1024
#: And a source's own lists and playlists (providers/client.py make_client):
#: the largest real ones are a hundred MB or two.
LIST_MAX = 512 * 1024 * 1024
#: How much of an answer that declares no charset its charset is guessed
#: from (``text``): requests' guess read all of it, and chardet takes time
#: with the square of a run of letters after one byte past ASCII - seconds
#: for 8 KB, an hour for a megabyte. 1 KB: 0.06 s at worst.
GUESS_BYTES = 1024

_lock = threading.Lock()
_made: dict[str, Any] = {}


def _classes() -> dict[str, Any]:
    """The capped session, adapter and error, made once the real module is
    in (never at import)."""
    with _lock:
        if _made:
            return _made
        import requests as real
        from requests.adapters import HTTPAdapter

        class ResponseTooLarge(real.exceptions.RequestException):
            """An answer past its ceiling: not read further."""

        class RedirectRefused(real.exceptions.RequestException):
            """A redirect core/dest_guard.py does not let on: not followed."""

        class DestinationRefused(real.exceptions.ConnectionError):
            """A request through a proxy whose name leads where
            core/dest_guard.py lets no request land: not sent."""

        class LoginHeld(real.exceptions.RequestException):
            """A request to dopeiptv.org that held a login of a source
            (core/site_out.py): not sent."""

        class OfflineRefused(real.exceptions.ConnectionError):
            """A request offline mode does not let out (core/offline.py):
            not sent - to its caller, a network that is not there."""

        from urllib3.connection import HTTPConnection, HTTPSConnection
        from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

        from . import dest_guard, offline, site_out

        class _Http(HTTPConnection):
            def _new_conn(self) -> Any:
                return dest_guard.check_peer(self, super()._new_conn())

        class _Https(HTTPSConnection):
            def _new_conn(self) -> Any:
                return dest_guard.check_peer(self, super()._new_conn())

        class _HttpPool(HTTPConnectionPool):
            ConnectionCls = _Http

        class _HttpsPool(HTTPSConnectionPool):
            ConnectionCls = _Https

        class _Counted:
            """An answer's body that ends the read past *limit* bytes."""

            def __init__(self, raw: Any, limit: int) -> None:
                self._raw, self._limit, self._seen = raw, limit, 0

            def _count(self, n: int) -> None:
                self._seen += n
                if self._seen > self._limit:
                    try:
                        self._raw.close()
                    except Exception:
                        pass
                    raise ResponseTooLarge(
                        f"the answer is larger than {self._limit >> 20} MB - not read further")

            def stream(self, amt: int = 2 ** 16, decode_content: Any = None) -> Any:
                for chunk in self._raw.stream(amt, decode_content=decode_content):
                    self._count(len(chunk))
                    yield chunk

            def read(self, amt: Any = None, *args: Any, **kw: Any) -> bytes:
                if amt is not None:
                    data = self._raw.read(amt, *args, **kw)
                    self._count(len(data))
                    return data
                parts: list[bytes] = []
                while True:
                    piece = self._raw.read(1 << 16, *args, **kw)
                    if not piece:
                        return b"".join(parts)
                    self._count(len(piece))
                    parts.append(piece)

            def __getattr__(self, name: str) -> Any:
                return getattr(self._raw, name)

        from . import safe_json, safe_xml

        class Response(real.Response):
            @property
            def apparent_encoding(self) -> Any:
                # From the answer's first GUESS_BYTES alone: of all of it,
                # minutes for an answer of a server that names no charset
                # (tests/test_fuzz_stalker.py).
                guess = getattr(real.compat, "chardet", None)
                if guess is None:
                    return "utf-8"
                return guess.detect(self.content[:GUESS_BYTES])["encoding"]

            @property
            def text(self) -> str:
                # A charset whose decoder takes time with the square of
                # the text (safe_xml.slow_codec) is no answer's: UTF-8.
                if safe_xml.slow_codec(getattr(self, "encoding", None)):
                    return str(self.content or b"", "utf-8", errors="replace")
                return str(super().text)

            def json(self, **kwargs: Any) -> Any:
                if not getattr(self, "encoding", None) and self.content:
                    # JSON is UTF-8 (or -16, -32, which its first bytes
                    # say: RFC 8259): one that declares no charset is read
                    # so, what is no UTF-8 replaced - requests guessed a
                    # charset from the bytes first, through ``text``, for
                    # every answer that was not UTF-8 (tests/test_fuzz_stalker.py).
                    self.encoding = real.utils.guess_json_utf(self.content) or "utf-8"
                try:
                    doc = super().json(**kwargs)
                except RecursionError:
                    raise real.exceptions.JSONDecodeError(
                        "nested deeper than any answer is", "", 0) from None
                # Held to one depth whatever Python reads (core/safe_json:
                # 3.12's json reads far deeper than 3.11's did).
                if safe_json.deeper_than(doc):
                    raise real.exceptions.JSONDecodeError(
                        "nested deeper than any answer is", "", 0)
                return doc

        class CappedAdapter(HTTPAdapter):
            """requests' own adapter, counting what is read of an answer
            that is not streamed to its caller."""

            #: The session's own ceiling, where it says one.
            max_response: int | None = None

            def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
                # Each connection is looked at once it is made, before a
                # byte is sent (core/dest_guard.py: where a name may land).
                super().init_poolmanager(*args, **kwargs)
                self.poolmanager.pool_classes_by_scheme = {"http": _HttpPool, "https": _HttpsPool}

            def send(self, request: Any, stream: bool = False, *args: Any, **kwargs: Any) -> Any:
                # requests' own order: timeout, verify, cert, proxies.
                proxies = kwargs.get("proxies", args[3] if len(args) > 3 else None)
                # Offline mode: only this computer and the home network,
                # whoever asks - a redirect's hop too (core/offline.py).
                if offline.on():
                    through = real.utils.select_proxy(request.url, proxies) if proxies else None
                    for address in (str(request.url or ""), str(through or "")):
                        why = offline.refusal(address) if address else ""
                        if why:
                            host = (offline._host_port(address) or ("?", 0))[0]
                            offline.note_refused(host)
                            raise OfflineRefused(f"offline mode: not sent to {host}",
                                                 request=request)
                # Nothing for dopeiptv.org that holds a login of a source,
                # whoever asks (core/site_out.py) - a redirect's hop too.
                try:
                    site_out.check(request.url, request.headers, request.body)
                except site_out.LoginRefused as e:
                    raise LoginHeld(str(e), request=request) from None
                if proxies and real.utils.select_proxy(request.url, proxies):
                    # Through a proxy the proxy connects (check_peer sees
                    # only it): the name is looked up here first - best
                    # effort; the proxy's own lookup is the proxy's.
                    why = dest_guard.proxied_refusal(request.url)
                    if why:
                        raise DestinationRefused(f"its name led to {why} - not sent",
                                                 request=request)
                r = super().send(request, stream, *args, **kwargs)
                if type(r) is real.Response:
                    r.__class__ = Response
                if (not stream or r.is_redirect) and r.raw is not None:
                    r.raw = _Counted(r.raw, self.max_response or RESPONSE_MAX)
                return r

        class Session(real.Session):
            """requests.Session on capped adapters. ``max_response``: this
            session's own ceiling, taken by whatever adapter it has (a
            route's too, core/net_adapter.py)."""

            def __init__(self) -> None:
                super().__init__()
                self._max_response: int | None = None
                self.mount("https://", CappedAdapter())
                self.mount("http://", CappedAdapter())

            @property
            def max_response(self) -> int | None:
                return self._max_response

            @max_response.setter
            def max_response(self, value: int | None) -> None:
                self._max_response = value
                for a in self.adapters.values():
                    if isinstance(a, CappedAdapter):
                        a.max_response = value

            def mount(self, prefix: str, adapter: Any) -> None:
                if isinstance(adapter, CappedAdapter) and getattr(self, "_max_response", None):
                    adapter.max_response = self._max_response
                super().mount(prefix, adapter)

            def get_redirect_target(self, resp: Any) -> Any:
                # Every redirect requests would follow - and the one it
                # only notes when a caller follows them by hand - is
                # looked at first (core/dest_guard.py).
                target = super().get_redirect_target(resp)
                if target:
                    nxt = real.compat.urljoin(resp.url or "", target)
                    why = dest_guard.redirect_refusal(resp.url or "", nxt)
                    if why:
                        try:
                            resp.close()
                        except Exception:
                            pass
                        raise RedirectRefused(f"a redirect to {why} - not followed")
                return target

        def request(method: str, url: str, **kwargs: Any) -> Any:
            with Session() as session:
                return session.request(method=method, url=url, **kwargs)

        def _verb(method: str) -> Any:
            def call(url: str, *args: Any, **kwargs: Any) -> Any:
                # requests.get(url, params), .post(url, data, json), .put/.patch(url, data)
                for name, value in zip(_POSITIONAL.get(method, ()), args, strict=False):
                    kwargs[name] = value
                if method == "head":
                    kwargs.setdefault("allow_redirects", False)
                return request(method, url, **kwargs)
            call.__name__ = method
            return call

        verbs = ("get", "post", "put", "patch", "delete", "head", "options")
        _made.update(Session=Session, session=Session, request=request,
                     CappedAdapter=CappedAdapter, ResponseTooLarge=ResponseTooLarge,
                     RedirectRefused=RedirectRefused, DestinationRefused=DestinationRefused,
                     LoginHeld=LoginHeld, OfflineRefused=OfflineRefused, Response=Response,
                     **{m: _verb(m) for m in verbs})
        # requests' own, as they were when it was imported: one replaced
        # since (a test's stand-in for the network) is the one to call.
        _made["_real"] = real
        _made["_own"] = {name: getattr(real, name) for name in ("Session", "session", "request", *verbs)}
        return _made


_POSITIONAL = {"get": ("params",), "post": ("data", "json"), "put": ("data",), "patch": ("data",)}
#: What this stand-in answers itself; every other name is requests' own.
_OWN = frozenset(("Session", "session", "request", "get", "post", "put", "patch", "delete",
                  "head", "options", "ResponseTooLarge", "RedirectRefused", "DestinationRefused",
                  "LoginHeld", "OfflineRefused"))


def capped_adapter() -> Any:
    """The adapter class an adapter of the app's own builds on
    (core/net_adapter.py)."""
    return _classes()["CappedAdapter"]


if TYPE_CHECKING:
    import requests as requests  # noqa: F401  (re-exported for type checkers)
else:
    class _LazyRequests:
        _mod = None

        def __getattr__(self, name):
            if name in _OWN:
                made = _classes()
                theirs = getattr(made["_real"], name, None)
                if name in made["_own"] and theirs is not made["_own"][name]:
                    return theirs
                return made[name]
            mod = _LazyRequests._mod
            if mod is None:
                import requests as mod
                _LazyRequests._mod = mod
            return getattr(mod, name)

    #: Import-free proxy; the first attribute access loads the real module.
    requests = _LazyRequests()
