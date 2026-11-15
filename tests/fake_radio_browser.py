"""A fake radio-browser.info server on the loopback, for the radio tests:
the lists the app asks for (top voted/clicked, countries, languages, tags,
the station search, a click) with a handful of stations, and every request
kept - its path, query and headers - to see what is sent."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

STATIONS = [
    {"stationuuid": "u-jazz", "name": "Jazz Radio", "url": "http://example.invalid/jazz",
     "url_resolved": "http://example.invalid/jazz.mp3", "favicon": "",
     "homepage": "https://jazz.example", "country": "Sweden", "countrycode": "SE",
     "language": "swedish", "tags": "jazz,smooth jazz", "codec": "MP3", "bitrate": 128,
     "hls": 0, "votes": 900},
    {"stationuuid": "u-rock", "name": "Rock FM", "url": "http://example.invalid/rock",
     "url_resolved": "", "favicon": "http://example.invalid/rock.png",
     "country": "Germany", "countrycode": "DE", "language": "german", "tags": "rock",
     "codec": "AAC", "bitrate": 96, "votes": 500},
    {"stationuuid": "u-news", "name": "News 24", "url": "https://example.invalid/news.m3u8",
     "country": "Sweden", "countrycode": "SE", "language": "swedish", "tags": "news,talk",
     "codec": "AAC+", "bitrate": 64, "hls": 1, "votes": 300},
    {"stationuuid": "", "name": "No id", "url": "http://example.invalid/x"},
    {"stationuuid": "u-bad", "name": "Not playable", "url": "ftp://example.invalid/x"},
]
COUNTRIES = [{"name": "Germany", "iso_3166_1": "DE", "stationcount": 1},
             {"name": "Sweden", "iso_3166_1": "SE", "stationcount": 2},
             {"name": "Nowhere", "iso_3166_1": "XX", "stationcount": 0}]
LANGUAGES = [{"name": "swedish", "iso_639": "sv", "stationcount": 2},
             {"name": "german", "iso_639": "de", "stationcount": 1}]
TAGS = [{"name": "jazz", "stationcount": 1}, {"name": "rock", "stationcount": 1},
        {"name": "news", "stationcount": 1}]


class FakeRadioBrowser:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[tuple[str, dict, dict]] = []
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                parts = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(parts.query).items()}
                srv.requests.append((parts.path, q, dict(self.headers)))
                if srv.status != 200:
                    return self._send(srv.status, {"error": "down"})
                self._send(200, srv.answer(parts.path, q))

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def answer(self, path: str, q: dict):
        if path in ("/json/stations/topvote", "/json/stations/topclick"):
            return STATIONS
        if path == "/json/stations/search":
            rows = STATIONS
            if q.get("name"):
                rows = [s for s in rows if q["name"].lower() in s["name"].lower()]
            if q.get("countrycode"):
                rows = [s for s in rows if s.get("countrycode") == q["countrycode"]]
            if q.get("language"):
                rows = [s for s in rows if s.get("language") == q["language"]]
            if q.get("tag"):
                rows = [s for s in rows if q["tag"] in str(s.get("tags", "")).split(",")]
            return rows
        if path == "/json/countries":
            return COUNTRIES
        if path == "/json/languages":
            return LANGUAGES
        if path == "/json/tags":
            return TAGS
        if path.startswith("/json/url/"):
            return {"ok": "true", "message": "retrieved station url",
                    "stationuuid": path.rsplit("/", 1)[1]}
        return []

    def paths(self) -> list[str]:
        return [p for p, _q, _h in self.requests]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
