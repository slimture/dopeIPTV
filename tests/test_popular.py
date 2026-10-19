"""Popular right now: TMDB's chart of the week, matched against what the
provider has (services/popular.py), and the chart fetch itself."""
import pytest

from dopeiptv.services import popular


def _keys(name):
    return [(k, rank, year) for k, rank, year in popular.name_keys(name)]


def test_name_keys_take_tags_brackets_and_years_off():
    assert _keys("EN - Dune: Part Two (2024) [4K]")[:2] == [
        ("en dune part two", 0, 2024), ("dune part two", 1, 2024)]
    assert ("nyckeln till frihet", 1, None) in _keys("|SE| Nyckeln till frihet")
    assert ("dune", 1, None) in _keys("EN|Dune")
    assert _keys("Ant-Man (2015)")[0] == ("ant man", 0, 2015)
    assert ("oppenheimer", 2, 2023) in _keys("4K-NORDIC | Oppenheimer (2023)")
    assert _keys("Amélie")[0] == ("amelie", 0, None)
    assert ("greys anatomy", 1, None) in _keys("NF - Grey's Anatomy")


def test_a_number_at_the_end_may_be_the_year_or_the_title():
    assert ("dune", 0, 2021) in _keys("Dune 2021")
    # Not a year at all: kept as part of the title.
    assert _keys("Blade Runner 2049") == [("blade runner 2049", 0, None)]
    # Could be either: looked up both ways.
    assert ("wonder woman", 0, 1984) in _keys("Wonder Woman 1984")
    assert ("wonder woman 1984", 0, None) in _keys("Wonder Woman 1984")
    # The year in brackets says which.
    assert _keys("Wonder Woman 1984 (2020)") == [
        ("wonder woman 1984", 0, 2020)]
    assert _keys("1917") == [("1917", 0, None)]


def test_norm_title_spellings_meet():
    n = popular.norm_title
    assert n("Spider-Man: No Way Home") == n("Spider Man No Way Home")
    assert n("Fast & Furious") == n("Fast and Furious")
    assert n("Amélie") == n("Amelie")
    assert n("Grey's Anatomy") == "greys anatomy"


CHART = [
    {"id": 693134, "titles": ["Dune: Part Two", "Dune: Del två"],
     "year": 2024},
    {"id": 27205, "titles": ["Inception"], "year": 2010},
    {"id": 1, "titles": ["Not On The Provider"], "year": 2024},
    {"id": 438631, "titles": ["Dune"], "year": 2021},
    {"id": 99, "titles": ["Wonder Woman 1984"], "year": 2020},
]


def test_match_keeps_the_chart_order_and_only_what_the_provider_has():
    cat = [
        {"stream_id": 1, "name": "EN - Inception (2010)"},
        {"stream_id": 2, "name": "SE - Dune: Del två (2024)"},
        {"stream_id": 3, "name": "EN - Dune: Part Two (2024)"},
        {"stream_id": 4, "name": "Dune (1984)"},
        {"stream_id": 5, "name": "Wonder Woman 1984"},
        {"stream_id": 6, "name": "Something Else"},
    ]
    groups = popular.match(CHART, cat)
    ids = [[r["stream_id"] for r in g] for g in groups]
    # Dune: Part Two in both languages; the 1984 Dune is not the 2021 one;
    # Wonder Woman 1984's number is its title.
    assert ids == [[2, 3], [1], [5]], ids


def test_a_tmdb_id_decides_over_the_title():
    cat = [
        # The provider says which film this is: not the chart's Dune.
        {"stream_id": 1, "name": "Dune", "tmdb": "841"},
        {"stream_id": 2, "name": "Dune Part Two", "tmdb_id": 693134},
        {"stream_id": 3, "name": "Inception"},
        {"stream_id": 4, "name": "Dune"},
    ]
    groups = popular.match(CHART, cat, known_id=lambda name: None)
    ids = [[r["stream_id"] for r in g] for g in groups]
    assert ids == [[2], [3], [4]], ids
    # An id the poster lookup already found counts as well.
    known = {"Inception": 999}
    groups = popular.match(CHART, cat[2:3], known_id=known.get)
    assert groups == []


def test_the_rows_own_year_field_counts():
    cat = [{"series_id": 7, "name": "Dune", "releaseDate": "1984-12-14"},
           {"series_id": 8, "name": "Dune", "year": "2021"}]
    groups = popular.match(CHART, cat)
    assert [[r["series_id"] for r in g] for g in groups] == [[8]]


def test_pick_takes_the_first_visible_copy_once():
    a, b, c = {"n": "a"}, {"n": "b"}, {"n": "c"}
    hidden = {id(a)}
    groups = [[a, b], [b, c], [c]]
    out = popular.pick(groups, lambda r: id(r) not in hidden, limit=10)
    assert out == [b, c]
    assert popular.pick(groups, lambda r: True, limit=1) == [a]


def test_chart_cache_round_trip_and_age(tmp_path):
    path = str(tmp_path / "sub" / "chart.json")
    data = {"t": 1000.0, "lang": "sv",
            "vod": [{"id": 1, "titles": ["A"], "year": 2024},
                    {"id": "bad"}],
            "series": []}
    popular.save(path, data)
    back = popular.load(path)
    assert back["vod"] == [{"id": 1, "titles": ["A"], "year": 2024}]
    assert popular.is_fresh(back, "sv", 1000 + 60)
    assert not popular.is_fresh(back, "en", 1000 + 60)       # language
    assert not popular.is_fresh(back, "sv", 1000 + popular.MAX_AGE_S + 1)
    assert popular.usable(back, 1000 + popular.MAX_AGE_S + 1)
    assert not popular.usable(back, 1000 + popular.SHOW_MAX_AGE_S + 1)
    assert not popular.is_fresh({}, "sv", 0) and not popular.usable(None, 0)
    (tmp_path / "broken.json").write_text("{nope")
    assert popular.load(str(tmp_path / "broken.json")) is None
    assert popular.load(str(tmp_path / "missing.json")) is None


def test_fetch_asks_for_english_and_the_apps_language():
    calls = []

    class Client:
        def trending(self, kind, pages, languages):
            calls.append((kind, pages, languages))
            return [{"id": 1, "titles": ["X"], "year": None}]

    data = popular.fetch(Client(), "sv", now=5.0)
    assert data["t"] == 5.0 and data["lang"] == "sv"
    assert calls == [("vod", popular.PAGES, ("en-US", "sv")),
                     ("series", popular.PAGES, ("en-US", "sv"))]
    calls.clear()
    popular.fetch(Client(), "en")
    assert calls[0][2] == ("en-US",)


def test_tmdb_trending_merges_titles_in_the_first_languages_order(
        monkeypatch):
    pytest.importorskip("PyQt6")
    from dopeiptv.providers import metadata

    pages = {
        ("en-US", 1): {"total_pages": 2, "results": [
            {"id": 10, "title": "Dune: Part Two", "original_title":
             "Dune: Part Two", "release_date": "2024-02-27"},
            {"id": 11, "name": "Shogun", "original_name": "SHOGUN",
             "first_air_date": "2024-02-27"}]},
        ("en-US", 2): {"total_pages": 2, "results": [
            {"id": 12, "title": "Inception", "release_date": ""}]},
        ("sv", 1): {"total_pages": 1, "results": [
            {"id": 10, "title": "Dune: Del två"},
            {"id": 77, "title": "Only In Swedish"}]},
    }
    asked = []

    class Resp:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    def fake_get(url, params=None, timeout=None):
        asked.append((url.rsplit("/", 2)[-2], params["language"],
                      params["page"]))
        return Resp(pages[(params["language"], params["page"])])

    monkeypatch.setattr(metadata.requests, "get", fake_get)
    rows = metadata.TmdbClient("k").trending("vod", 4, ("en-US", "sv"))
    assert [r["id"] for r in rows] == [10, 11, 12]
    assert rows[0]["titles"] == ["Dune: Part Two", "Dune: Del två"]
    assert rows[0]["year"] == 2024 and rows[2]["year"] is None
    assert rows[1]["titles"] == ["Shogun", "SHOGUN"]
    # The last page stops the paging; movies come from /trending/movie.
    assert asked == [("movie", "en-US", 1), ("movie", "en-US", 2),
                     ("movie", "sv", 1)]
