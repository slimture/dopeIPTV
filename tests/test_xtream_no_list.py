"""An Xtream answer that is no list. A panel that refuses the login answers a
list action with {"user_info": {"auth": 0}}; the category list walked that
object's keys as rows and crashed ('str' object has no attribute 'get',
error report adf7cdea1d11, 1.5.0 on Windows). Now it is a failed request:
never stored, the last good copy served if there is one, else the reason."""
import pytest

from dopeiptv.providers.client import NoList, XtreamClient, list_rows

REFUSED = {"user_info": {"auth": 0, "status": None}}
CATS = [{"category_id": "1", "category_name": "News"},
        {"category_id": "2", "category_name": "Sport"}]


def _client(tmp_path, answers):
    c = XtreamClient("http://h.tv", "u", "p",
                     cache_path=str(tmp_path / "lists_x.json"))
    asked = []

    def api(**params):
        asked.append(params.get("action"))
        return answers.pop(0)
    c._api = api
    return c, asked


def test_rows_of_an_answer():
    assert list_rows(CATS) is CATS
    assert list_rows([CATS[0], "x", 3, None]) == [CATS[0]]
    assert list_rows({"1": CATS[0], "2": CATS[1]}) == CATS
    assert list_rows(REFUSED) is None
    assert list_rows({"error": "nope"}) is None
    assert list_rows({}) is None
    assert list_rows("<html>") is None
    assert list_rows(None) is None


def test_a_refused_login_is_no_category_list(tmp_path):
    c, _ = _client(tmp_path, [REFUSED])
    with pytest.raises(NoList) as e:
        c.live_categories()
    assert str(e.value) == "Wrong username or password."


def test_nothing_is_stored_and_the_last_good_copy_is_served(tmp_path):
    c, asked = _client(tmp_path, [CATS, REFUSED, CATS])
    assert c.vod_categories() == CATS
    c.clear_list_cache()
    assert c.vod_categories() == CATS          # the refusal: the copy from before
    c.clear_list_cache()
    assert c.vod_categories() == CATS          # asked again, not a stored refusal
    assert asked == ["get_vod_categories"] * 3


def test_an_old_copy_that_is_no_list_is_none(tmp_path):
    c, _ = _client(tmp_path, [])
    c._list_cache[("get_series_categories",)] = (9e18, REFUSED)
    assert c.series_categories() == []
    c._list_cache[("get_series", None)] = (9e18, REFUSED)
    assert c.held_series() is None


def test_other_answers_that_are_no_list(tmp_path):
    c, _ = _client(tmp_path, [{"error": "maintenance"}])
    with pytest.raises(NoList) as e:
        c.live_streams("7")
    assert e.value.key == "err_unexpected_response"


def test_rows_keyed_by_id_are_rows(tmp_path):
    c, _ = _client(tmp_path, [{"10": {"stream_id": 10}, "11": {"stream_id": 11}}])
    assert [r["stream_id"] for r in c.live_streams()] == [10, 11]
