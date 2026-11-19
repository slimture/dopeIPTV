"""The front proxy's security headers (deploy/proxy-dopeiptv.org.conf): on
every answer (always), the CSP named again in the server - nginx gives a
server the http block's add_header lines only while it has none of its own -
and the address kept to the site on the admin, support and Trakt pages; no
version and no log in any of the site's servers, the old name's too; a body
held in memory, a few sync copies at once, no answer in a temporary file.
And deploy/proxy-headers.sh,
which puts into the live files what they lack of all that, keeping their
own addresses: from a file before any of it, and from one an earlier run
made."""
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONF = os.path.join(ROOT, "deploy", "proxy-dopeiptv.org.conf")
OLD = os.path.join(ROOT, "deploy", "proxy-iptv.dope.rs.conf")
SCRIPT = os.path.join(ROOT, "deploy", "proxy-headers.sh")
sys.path.insert(0, HERE)

from test_deploy_content import _effective, conf, locate, map_value, parse  # noqa: E402


def _servers(text: str) -> dict:
    out = {}
    for m in re.finditer(r"^server \{\n(.*?)^\}", text, re.S | re.M):
        name = re.search(r"server_name ([^;]+);", m.group(1)).group(1)
        out[name] = m.group(1)
    return out


def _by_name(top) -> dict:
    """Each server by its server_name, " +ssl" after one that listens so."""
    out = {}
    for b in top.all("server"):
        ssl = any("ssl" in d.args for d in b.all("listen"))
        out[" ".join(b.one("server_name").args) + (" +ssl" if ssl else "")] = b
    return out


SITE_HEADERS = ("add_header Content-Security-Policy   $csp_policy always;",
                "add_header Content-Security-Policy   \"frame-ancestors 'none'\" always;",
                'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;',
                'add_header X-Content-Type-Options    "nosniff" always;',
                'add_header X-Frame-Options           "DENY" always;',
                "add_header Referrer-Policy           $dopeiptv_referrer always;",
                "proxy_hide_header Referrer-Policy;",
                "add_header Cross-Origin-Opener-Policy   $dopeiptv_coop always;",
                "add_header Cross-Origin-Resource-Policy $dopeiptv_corp always;")


def test_every_answer_of_the_site_carries_its_security_headers():
    text = open(CONF, encoding="utf-8").read()
    site = _servers(text)["dopeiptv.org"]
    for line in SITE_HEADERS:
        assert line in site, line
    # A location with an add_header of its own would lose them all.
    for loc in re.findall(r"location [^{]+\{(.*?)\n    \}", site, re.S):
        assert "add_header" not in loc
    assert 'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;' \
        in _servers(text)["www.dopeiptv.org"]
    refer = re.search(r"map \$uri \$dopeiptv_referrer \{(.*?)\}", text, re.S).group(1)
    for path in ("/admin", "/support", "/trakt/"):
        assert re.search(r"~\^%s\S*\s+\"no-referrer\";" % re.escape(path), refer), path


def test_the_admin_pages_and_the_services_are_the_sites_own():
    top = conf(CONF)
    # Opener: the admin pages only (a payment's window talks to the page).
    for uri in ("/admin", "/admin/", "/admin/login"):
        assert map_value(top, "$dopeiptv_coop", uri) == "same-origin", uri
    for uri in ("/", "/support/", "/help/", "/administrator"):
        assert map_value(top, "$dopeiptv_coop", uri) == "", uri
    # Resource: the admin pages and what the services answer; never a page,
    # a file, the release list or a download.
    for uri in ("/admin/", "/admin/stats", "/sync/" + "ab" * 32, "/together/x", "/feedback/report",
                "/feedback/replies", "/donate/config", "/support/key", "/hit"):
        assert map_value(top, "$dopeiptv_corp", uri) == "same-origin", uri
    for uri in ("/", "/support/", "/releases.json", "/nightly.json", "/dl.php", "/files/SHA256SUMS",
                "/style.css", "/.well-known/security.txt", "/hits", "/support/keys"):
        assert map_value(top, "$dopeiptv_corp", uri) == "", uri
    policy = next(" ".join(b.args) for b in _by_name(top)["dopeiptv.org +ssl"].all("add_header")
                  if b.args[0] == "Permissions-Policy")
    named = set(re.findall(r"([a-z-]+)=\(\)", policy))
    assert {"camera", "microphone", "geolocation", "usb"} <= named
    # What the site and Stripe use stays: a tip (payment), passkeys, Copy,
    # Send the link.
    for kept in ("payment", "publickey-credentials-get", "publickey-credentials-create",
                 "clipboard-write", "web-share"):
        assert kept not in policy, kept
    assert policy.endswith(" always")


def test_every_server_of_the_site_says_no_version_and_keeps_no_log():
    servers = {**_by_name(conf(CONF)), **_by_name(conf(OLD))}
    assert set(servers) == {"dopeiptv.org +ssl", "www.dopeiptv.org +ssl", "dopeiptv.org www.dopeiptv.org",
                            "iptv.dope.rs +ssl", "iptv.dope.rs"}
    for name, srv in servers.items():
        assert srv.values("server_tokens") == ["off"], name
        assert srv.values("access_log") == ["off"], name
        assert srv.values("error_log") == ["/var/log/nginx/error.log crit"], name
    # https only on the old name's answers too (its move to the new name).
    hsts = 'Strict-Transport-Security max-age=31536000; includeSubDomains; preload always'
    assert servers["iptv.dope.rs +ssl"].values("add_header") == [hsts]
    assert servers["iptv.dope.rs +ssl"].values("return") == ["301 https://dopeiptv.org$request_uri"]


def test_a_body_is_held_in_memory_and_a_few_sync_copies_at_once():
    top = conf(CONF)
    srv = _by_name(top)["dopeiptv.org +ssl"]
    content = conf()
    csrv = next(b for b in content.all("server") if "dopeiptv.org" in b.values("server_name")[0])
    sizes = {"k": 1024, "m": 1 << 20}

    def size(v: str) -> int:
        return int(v[:-1]) * sizes[v[-1]]
    for uri in ("/", "/admin/pick", "/feedback/report", "/sync/" + "ab" * 32, "/together/x", "/donate/session",
                "/style.css"):
        allowed = size(_effective(srv, uri, "client_max_body_size"))
        # Never less than the content server lets through, all of it in memory.
        assert allowed >= size(_effective(csrv, uri, "client_max_body_size")), uri
        assert size(_effective(srv, uri, "client_body_buffer_size")) >= allowed, uri
    assert _effective(srv, "/admin/pick", "client_max_body_size") == "256k"
    assert _effective(srv, "/sync/x", "client_max_body_size") == "3m"
    assert _effective(srv, "/together/x", "client_max_body_size") == "4k"
    sync = locate(srv, "/sync/x")[-1]
    assert sync.values("limit_conn") == ["conn_limit 64", "dopeiptv_sync_put 8"]
    assert map_value(top, "$dopeiptv_sync_put", "PUT") == "$binary_remote_addr"
    for method in ("GET", "DELETE", "HEAD", "POST"):
        assert map_value(top, "$dopeiptv_sync_put", method) == "", method
    assert [b.args for b in top.all("limit_conn_zone")] == [["$dopeiptv_sync_put", "zone=dopeiptv_sync_put:1m"]]
    # The other places keep their single limit.
    assert locate(srv, "/together/x")[-1].values("limit_conn") == ["conn_limit 64"]


# -- deploy/proxy-headers.sh -----------------------------------------------------

def _git(ref: str, path: str) -> str:
    got = subprocess.run(["git", "-C", ROOT, "show", f"{ref}:{path}"], capture_output=True, text=True)
    if got.returncode:
        pytest.skip("no git history here")
    return got.stdout


def _proxy(tmp_path, site: str, old: str | None = None) -> tuple:
    """A proxy's nginx folder: an http block with a header of its own and the
    CSP, the site's file with its real address, the old name's if given."""
    ngx = tmp_path / "nginx"
    (ngx / "conf.d").mkdir(parents=True)
    (ngx / "nginx.conf").write_text("http {\n    include csp-map.conf;\n    add_header Content-Security-Policy $csp_policy;\n"
                                    "    add_header X-Global \"1\";\n    server { location /f { add_header X-Inner 2; } }\n"
                                    "    include conf.d/*.conf;\n}\n")
    (ngx / "csp-map.conf").write_text("map $server_name $csp_policy { default \"x\"; }\n")
    live = ngx / "conf.d" / "dopeiptv.org.conf"
    live.write_text(site.replace("CONTENT_HOST", "10.0.0.5"))
    if old is not None:
        (ngx / "conf.d" / "iptv.dope.rs.conf").write_text(old)
    keep = tmp_path / "keep"
    keep.mkdir()
    env = dict(os.environ, NGINX_DIR=str(ngx), NGINX_TEST="true", NGINX_RELOAD="true", BACKUP_DIR=str(keep))
    return ngx, keep, env


def _run(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", SCRIPT], env=env, capture_output=True, text=True)


def _flat(b) -> str:
    return repr((b.name, b.args, sorted(_flat(c) for c in b.body) if b.body is not None else None))


def _shape(text: str) -> dict:
    """What nginx makes of a file, the order of lines aside: each server's
    and each location's directives, each map and zone."""
    top = parse(text)
    out = {name: sorted(_flat(d) for d in srv.body) for name, srv in _by_name(top).items()}
    out["(top)"] = sorted(_flat(b) for b in top.body if b.name != "server")
    return out


def _wanted(extra: str = 'add_header X-Global "1";') -> dict:
    """The repo's file as the proxy has it: its real address, and the http
    block's own header named again in the site's server."""
    shape = _shape(open(CONF, encoding="utf-8").read().replace("CONTENT_HOST", "10.0.0.5"))
    shape["dopeiptv.org +ssl"] = sorted(shape["dopeiptv.org +ssl"] + [_flat(parse(extra).body[0])])
    return shape


def test_the_script_puts_in_what_the_file_shows_and_keeps_the_rest(tmp_path):
    # The proxy's file as it was before the headers (CONTENT_HOST already
    # the real address): everything the repo's file has, an add_header of
    # the http block named again, the addresses untouched, a copy kept, a
    # second run a no-op.
    before = _git("39752db", "deploy/proxy-dopeiptv.org.conf")
    ngx, keep, env = _proxy(tmp_path, before)
    live = ngx / "conf.d" / "dopeiptv.org.conf"
    run = _run(env)
    assert run.returncode == 0, run.stderr + run.stdout
    assert 'kept from the http block: add_header X-Global "1";' in run.stdout and "X-Inner" not in run.stdout
    got = live.read_text()
    want = open(CONF, encoding="utf-8").read()
    for line in [ln for ln in want.splitlines() if not ln.lstrip().startswith("#")
                 and re.search(r"add_header|proxy_hide_header|no-referrer|same-origin|server_tokens"
                               r"|client_body_buffer_size|client_max_body_size|proxy_max_temp_file_size"
                               r"|_log |limit_conn", ln)]:
        assert line.replace("CONTENT_HOST", "10.0.0.5") in got, line
    assert _shape(got) == _wanted()
    assert '    add_header X-Global "1";' in _servers(got)["dopeiptv.org"]
    assert "CONTENT_HOST" not in got and got.count("10.0.0.5:8081") == 4
    assert [p.read_text() for p in keep.iterdir()] == [before.replace("CONTENT_HOST", "10.0.0.5")]
    again = _run(env)
    assert again.returncode == 0 and "nothing changed" in again.stdout and live.read_text() == got
    assert len(list(keep.iterdir())) == 1


def test_a_file_an_earlier_run_made_gets_what_is_new(tmp_path):
    # As the proxy has it today: the headers in (the script stopped at once
    # on such a file), the old name's file with its own certificate.
    site = _git("e75c922", "deploy/proxy-dopeiptv.org.conf")
    old = _git("e75c922", "deploy/proxy-iptv.dope.rs.conf").replace("YOURCERT", "iptv.dope.rs")
    ngx, keep, env = _proxy(tmp_path, site, old)
    run = _run(env)
    assert run.returncode == 0, run.stderr + run.stdout
    assert _shape((ngx / "conf.d" / "dopeiptv.org.conf").read_text()) == _wanted()
    got_old = (ngx / "conf.d" / "iptv.dope.rs.conf").read_text()
    assert _shape(got_old) == _shape(open(OLD, encoding="utf-8").read().replace("YOURCERT", "iptv.dope.rs"))
    assert "live/iptv.dope.rs/fullchain.pem" in got_old
    assert sorted(p.name.split(".conf.")[0] for p in keep.iterdir()) == ["dopeiptv.org", "iptv.dope.rs"]
    again = _run(env)
    assert again.returncode == 0 and "nothing changed" in again.stdout
    assert len(list(keep.iterdir())) == 2


def test_a_value_of_the_live_files_own_is_kept_and_said(tmp_path):
    site = _git("e75c922", "deploy/proxy-dopeiptv.org.conf").replace(
        "    proxy_hide_header Referrer-Policy;\n",
        "    proxy_hide_header Referrer-Policy;\n    client_max_body_size 1m;\n", 1)
    ngx, keep, env = _proxy(tmp_path, site)
    run = _run(env)
    assert run.returncode == 0, run.stderr + run.stdout
    got = (ngx / "conf.d" / "dopeiptv.org.conf").read_text()
    assert "kept as it is in dopeiptv.org +ssl: client_max_body_size 1m;" in run.stdout
    srv = _by_name(parse(got))["dopeiptv.org +ssl"]
    assert srv.values("client_max_body_size") == ["1m"] and srv.values("client_body_buffer_size") == ["256k"]


def test_nothing_stays_changed_when_nginx_refuses_it(tmp_path):
    site = _git("e75c922", "deploy/proxy-dopeiptv.org.conf")
    old = _git("e75c922", "deploy/proxy-iptv.dope.rs.conf")
    ngx, keep, env = _proxy(tmp_path, site, old)
    run = _run(dict(env, NGINX_TEST="false", NGINX_RELOAD="echo RELOAD-RAN"))
    assert run.returncode == 1 and "nothing reloaded" in run.stdout and "RELOAD-RAN" not in run.stdout
    assert (ngx / "conf.d" / "dopeiptv.org.conf").read_text() == site.replace("CONTENT_HOST", "10.0.0.5")
    assert (ngx / "conf.d" / "iptv.dope.rs.conf").read_text() == old
