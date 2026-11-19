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
