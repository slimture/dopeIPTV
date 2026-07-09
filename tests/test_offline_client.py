"""OfflineClient (first-run 'explore' mode) must be a faithful empty stand-in
for XtreamClient: same interface, every data call empty, nothing hits the net."""

import inspect

from dopeiptv.providers.client import OfflineClient, XtreamClient


def test_same_public_interface_as_xtream():
    """Every public method on XtreamClient must exist on OfflineClient, so the
    window never hits a missing attribute when running without a provider."""
    def public_methods(cls):
        return {n for n, _ in inspect.getmembers(cls, inspect.isfunction)
                if not n.startswith("_")}
    missing = public_methods(XtreamClient) - public_methods(OfflineClient)
    assert not missing, f"OfflineClient missing: {sorted(missing)}"


def test_data_calls_return_empty():
    c = OfflineClient()
    assert c.live_categories() == []
    assert c.vod_categories() == []
    assert c.series_categories() == []
    assert c.live_streams("1") == []
    assert c.vod_streams("1") == []
    assert c.series_list("1") == []
    assert c.series_info(1) == {}
    assert c.vod_info(1) == {}
    assert c.short_epg(1) == []
    assert c.epg_table(1) == []
    assert c.xmltv() == b""


def test_url_builders_return_empty_string():
    c = OfflineClient()
    assert c.live_url(1) == ""
    assert c.vod_url(1) == ""
    assert c.episode_url(1) == ""


def test_carries_the_attributes_the_epg_and_ui_read():
    c = OfflineClient()
    assert c.server == "" and c.username == "" and c.password == ""
    assert c.session is not None          # epg download reads client.session
    assert c.authenticate() == {}          # no-op, never raises
