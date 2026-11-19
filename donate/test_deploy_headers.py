"""The front proxy's security headers (deploy/proxy-dopeiptv.org.conf): on
every answer (always), the CSP named again in the server - nginx gives a
server the http block's add_header lines only while it has none of its own -
and the address kept to the site on the admin, support and Trakt pages."""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(os.path.dirname(HERE), "deploy", "proxy-dopeiptv.org.conf")


def _servers(text: str) -> dict:
    out = {}
    for m in re.finditer(r"^server \{\n(.*?)^\}", text, re.S | re.M):
        name = re.search(r"server_name ([^;]+);", m.group(1)).group(1)
        out[name] = m.group(1)
    return out


def test_every_answer_of_the_site_carries_its_security_headers():
    text = open(CONF, encoding="utf-8").read()
    site = _servers(text)["dopeiptv.org"]
    for line in ("add_header Content-Security-Policy   $csp_policy always;",
                 "add_header Content-Security-Policy   \"frame-ancestors 'none'\" always;",
                 'add_header Strict-Transport-Security "max-age=31536000" always;',
                 'add_header X-Content-Type-Options    "nosniff" always;',
                 'add_header X-Frame-Options           "DENY" always;',
                 "add_header Referrer-Policy           $dopeiptv_referrer always;",
                 "proxy_hide_header Referrer-Policy;"):
        assert line in site, line
    # A location with an add_header of its own would lose them all.
    for loc in re.findall(r"location [^{]+\{(.*?)\n    \}", site, re.S):
        assert "add_header" not in loc
    assert 'add_header Strict-Transport-Security "max-age=31536000" always;' in _servers(text)["www.dopeiptv.org"]
    refer = re.search(r"map \$uri \$dopeiptv_referrer \{(.*?)\}", text, re.S).group(1)
    for path in ("/admin", "/support", "/trakt/"):
        assert re.search(r"~\^%s\S*\s+\"no-referrer\";" % re.escape(path), refer), path


def test_the_script_puts_in_what_the_file_shows_and_keeps_the_rest(tmp_path):
    # deploy/proxy-headers.sh on the proxy's file as it was before the
    # headers (CONTENT_HOST already the real address): the same header
    # lines as the repo's file, an add_header of the http block named
    # again, the addresses untouched, a copy kept, a second run a no-op.
    import shutil
    import subprocess
    before = subprocess.run(["git", "-C", os.path.dirname(HERE), "show", "39752db:deploy/proxy-dopeiptv.org.conf"],
                            capture_output=True, text=True)
    if before.returncode:
        import pytest
        pytest.skip("no git history here")
    ngx = tmp_path / "nginx"
    (ngx / "conf.d").mkdir(parents=True)
    (ngx / "nginx.conf").write_text("http {\n    include csp-map.conf;\n    add_header Content-Security-Policy $csp_policy;\n"
                                    "    add_header X-Global \"1\";\n    server { location /f { add_header X-Inner 2; } }\n"
                                    "    include conf.d/*.conf;\n}\n")
    (ngx / "csp-map.conf").write_text("map $server_name $csp_policy { default \"x\"; }\n")
    live = ngx / "conf.d" / "dopeiptv.org.conf"
    live.write_text(before.stdout.replace("CONTENT_HOST", "10.0.0.5"))
    keep = tmp_path / "keep"
    keep.mkdir()
    env = dict(os.environ, NGINX_DIR=str(ngx), NGINX_TEST="true", NGINX_RELOAD="true", BACKUP_DIR=str(keep))
    script = os.path.join(os.path.dirname(HERE), "deploy", "proxy-headers.sh")
    run = subprocess.run(["sh", script], env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr + run.stdout
    assert 'kept from the http block: add_header X-Global "1";' in run.stdout and "X-Inner" not in run.stdout
    got = live.read_text()
    want = open(CONF, encoding="utf-8").read()
    for line in [ln for ln in want.splitlines() if not ln.lstrip().startswith("#")
                 and ("add_header" in ln or "proxy_hide_header" in ln or "no-referrer" in ln)]:
        assert line in got, line
    assert '    add_header X-Global "1";' in _servers(got)["dopeiptv.org"]
    assert "CONTENT_HOST" not in got and got.count("10.0.0.5:8081") == 4
    assert [p.read_text() for p in keep.iterdir()] == [before.stdout.replace("CONTENT_HOST", "10.0.0.5")]
    again = subprocess.run(["sh", script], env=env, capture_output=True, text=True)
    assert again.returncode == 0 and "nothing changed" in again.stdout and live.read_text() == got
    assert shutil.which("sh")
