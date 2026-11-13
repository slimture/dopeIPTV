"""The update channels: Releases follows releases only; Test builds also
takes the nightly pre-releases, whose versions slot between releases."""
from __future__ import annotations

import pytest

from dopeiptv.core import selfupdate, updates
from dopeiptv.core.updates import (
    fetch_latest_nightly, fetch_update, is_newer, offered)


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
    # "site": dopeiptv.org's releases.json, or None for a site that does not
    # answer (then GitHub's latest release is asked, as these tests mostly do).
    state = {"latest": rel("v1.4.0"), "all": [], "site": None, "asked": []}

    class Fake:
        @staticmethod
        def get(url, params=None, headers=None, timeout=None):
            assert headers["User-Agent"].startswith("dopeIPTV/")
            state["asked"].append(url)
            if url == "https://dopeiptv.org/releases.json":
                if state["site"] is None:
                    raise OSError("no answer")
                return Resp(state["site"])
            if url.endswith("/releases/latest"):
                return Resp(state["latest"])
            assert url.endswith("/releases")
            return Resp(state["all"])

    monkeypatch.setattr(updates, "requests", Fake)
    return state


def site_json(version, names=("x86_64.AppImage",)):
    return {"version": version, "notes": f"notes {version}",
            "html_url": f"https://github.invalid/v{version}",
            "assets": [{"name": f"dopeIPTV-{version}-{n}", "size": 7, "sha256": "ab" * 32,
                        "url": f"/files/dopeIPTV-{version}-{n}"} for n in names]}


def test_a_release_comes_from_dopeiptv_org_and_is_fetched_through_it(github):
    github["site"] = site_json("1.4.1")
    r = fetch_update(channel="stable")
    assert r["tag"] == "v1.4.1" and r["nightly"] is False
    assert github["asked"] == ["https://dopeiptv.org/releases.json"]   # GitHub not asked
    a = selfupdate.pick_asset(r["assets"], r["tag"], "x86_64")
    # Through /dl.php, which the site counts, as an update from the app.
    assert a["url"] == "https://dopeiptv.org/dl.php?f=dopeIPTV-1.4.1-x86_64.AppImage"
    assert a["size"] == 7 and a["digest"] == "sha256:" + "ab" * 32


def test_github_answers_when_the_site_does_not(github):
    github["site"] = None
    assert fetch_update(channel="stable")["tag"] == "v1.4.0"
    github["site"] = {"version": "", "assets": []}              # a broken file
    assert fetch_update(channel="stable")["tag"] == "v1.4.0"


def test_test_builds_still_come_from_github_beside_the_sites_release(github):
    github["site"] = site_json("1.4.0")
    github["all"] = [rel("nightly-1.4.0.15", prerelease=True)]
    assert fetch_update(channel="nightly")["tag"] == "v1.4.0.15"
    github["site"] = site_json("1.4.1")
    assert fetch_update(channel="nightly")["tag"] == "v1.4.1"


def test_the_user_agent_names_the_app_and_its_version():
    from dopeiptv import VERSION
    assert updates.user_agent().startswith(f"dopeIPTV/{VERSION} (")


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


def test_back_on_releases_the_release_is_offered_to_a_test_build():
    # On Test builds a release never "downgrades" a newer build...
    assert not offered("v1.4.0", "1.4.0.12", "nightly")
    assert offered("v1.4.0.13", "1.4.0.12", "nightly")
    # ...back on Releases, the release the build carries the number of is
    # offered, and anything after it; an older release is not.
    assert offered("v1.4.0", "1.4.0.12", "stable")
    assert offered("v1.4.1", "1.4.0.12", "stable")
    assert not offered("v1.3.0", "1.4.0.12", "stable")
    # A release is compared as before; nothing offered without a tag.
    assert offered("v1.4.1", "1.4.0", "stable")
    assert not offered("v1.4.0", "1.4.0", "stable")
    assert not offered("", "1.4.0.12", "stable")
