"""The update channels: Releases follows releases only; Test builds also
takes the nightly pre-releases, whose versions slot between releases."""
from __future__ import annotations

import pytest

from dopeiptv.core import selfupdate, updates
from dopeiptv.core.updates import fetch_latest_nightly, fetch_update, is_newer


class Resp:
    def __init__(self, data) -> None:
        self.data = data

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self.data


def rel(tag, prerelease=False, draft=False):
    return {"tag_name": tag, "name": tag, "body": f"notes {tag}",
            "html_url": f"https://github.invalid/{tag}",
            "prerelease": prerelease, "draft": draft,
            "assets": [{"name": f"dopeIPTV-{tag.split('-', 1)[-1].lstrip('v')}"
                                "-x86_64.AppImage",
                        "browser_download_url": "https://x.invalid/a",
                        "size": 1, "digest": "sha256:" + "0" * 64}]}


@pytest.fixture
def github(monkeypatch):
    state = {"latest": rel("v1.4.0"), "all": []}

    class Fake:
        @staticmethod
        def get(url, params=None, headers=None, timeout=None):
            assert headers["User-Agent"] == "dopeIPTV"
            if url.endswith("/releases/latest"):
                return Resp(state["latest"])
            assert url.endswith("/releases")
            return Resp(state["all"])

    monkeypatch.setattr(updates, "requests", Fake)
    return state


def test_a_nightly_slots_between_releases():
    assert is_newer("v1.4.0.123", "1.4.0")
    assert is_newer("v1.4.0.124", "1.4.0.123")
    assert is_newer("v1.4.1", "1.4.0.123")
    assert not is_newer("v1.4.0", "1.4.0.123")


def test_the_newest_nightly_and_nothing_else(github):
    github["all"] = [
        rel("nightly-1.4.0.12", prerelease=True),
        rel("nightly-1.4.0.15", prerelease=True),
        rel("nightly-1.4.0.16", prerelease=True, draft=True),   # not done
        rel("v1.4.0"),                                          # a release
        rel("beta-9.9.9", prerelease=True),                     # not ours
    ]
    n = fetch_latest_nightly()
    assert n["tag"] == "v1.4.0.15" and n["nightly"] is True
    assert n["url"].endswith("nightly-1.4.0.15")


def test_releases_never_see_a_nightly(github):
    github["all"] = [rel("nightly-1.4.0.15", prerelease=True)]
    assert fetch_update(channel="stable")["tag"] == "v1.4.0"


def test_test_builds_take_the_newer_of_the_two(github):
    github["all"] = [rel("nightly-1.4.0.15", prerelease=True)]
    assert fetch_update(channel="nightly")["tag"] == "v1.4.0.15"
    github["latest"] = rel("v1.4.1")          # a release since
    assert fetch_update(channel="nightly")["tag"] == "v1.4.1"
    github["all"] = []
    assert fetch_update(channel="nightly")["tag"] == "v1.4.1"


def test_the_appimage_of_a_nightly_is_found_by_its_version(github):
    github["all"] = [rel("nightly-1.4.0.15", prerelease=True)]
    n = fetch_latest_nightly()
    asset = selfupdate.pick_asset(n["assets"], n["tag"], "x86_64")
    assert asset is not None
    assert asset["name"] == "dopeIPTV-1.4.0.15-x86_64.AppImage"
