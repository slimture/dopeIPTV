"""Values from outside that are no values a real source sends - an encoding
that is no text encoding, a number too long or too large to be one - are
passed over where they stand: the source, the guide or the answer they came
in goes on as before."""
from __future__ import annotations

from dopeiptv.services import share


def _guide(enc: str) -> list[bytes]:
    return [f'<?xml version="1.0" encoding="{enc}"?>\n<tv>'.encode(),
            b'<channel id="a"><display-name>A</display-name></channel></tv>\n']


def test_a_guide_naming_a_codec_that_is_no_text_encoding_is_passed_on_as_it_is():
    for enc in ("zlib", "bz2", "base64", "hex", "rot13", "uu", "quopri", "idna", "nonsense"):
        body = b"".join(share.guide_body(iter(_guide(enc))))
        assert b'<channel id="a">' in body, enc
    # A real one is still decoded into UTF-8.
    latin = [b'<?xml version="1.0" encoding="ISO-8859-1"?>\n<tv>',
             b'<channel id="a"><display-name>K\xe4nal</display-name></channel></tv>\n']
    assert "Känal".encode() in b"".join(share.guide_body(iter(latin)))
