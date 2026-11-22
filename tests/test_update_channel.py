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
    state = {"latest": rel("v1.4.0"), "all": [], "site": None, "site_nightly": None, "asked": []}

    class Fake:
        @staticmethod
        def get(url, params=None, headers=None, timeout=None, allow_redirects=True):
            assert allow_redirects is False, "every redirect followed by hand, or none"
            assert headers["User-Agent"].startswith("dopeIPTV/")
            state["asked"].append(url)
            if url == "https://dopeiptv.org/releases.json":
                if state["site"] is None:
                    raise OSError("no answer")
                return Resp(state["site"])
            if url == "https://dopeiptv.org/nightly.json":
                if state["site_nightly"] is None:
                    raise OSError("404")
                return Resp(state["site_nightly"])
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


def test_releases_never_take_a_test_build_whoever_names_one(github):
    """A test build is signed with the release key, so only its version keeps
    it off Releases: the site's releases.json naming one is not believed."""
    github["site"] = site_json("1.6.6.40")
    assert fetch_update(channel="stable")["tag"] == "v1.4.0", "GitHub's release instead"
    assert any("api.github.com" in u for u in github["asked"])
    github["latest"] = rel("v1.6.6.40")
    with pytest.raises(ValueError):
        fetch_update(channel="stable")
    github["site"], github["latest"] = None, rel("v1.4.0")
    github["site_nightly"] = site_json("1.6.6.41")
    assert fetch_update(channel="nightly")["tag"] == "v1.6.6.41", "Test builds as before"


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


@pytest.fixture
def left():
    updates.leave_nightly("")
    yield updates.leave_nightly
    updates.leave_nightly("")


def test_an_update_is_strictly_newer_on_either_channel(left):
    # On Test builds a release never "downgrades" a newer build...
    assert not offered("v1.4.0", "1.4.0.12", "nightly")
    assert offered("v1.4.0.13", "1.4.0.12", "nightly")
    assert not offered("v1.4.0.12", "1.4.0.12", "nightly")
    # ...and on Releases a test build gets only what is newer than it: the
    # release it carries the number of sorts below it.
    assert not offered("v1.4.0", "1.4.0.12", "stable")
    assert offered("v1.4.1", "1.4.0.12", "stable")
    assert not offered("v1.3.0", "1.4.0.12", "stable")
    # A release is compared as ever; nothing offered without a tag.
    assert offered("v1.4.1", "1.4.0", "stable")
    assert not offered("v1.4.0", "1.4.0", "stable")
    assert not offered("v1.3.9", "1.4.0", "stable")
    assert not offered("", "1.4.0.12", "stable")


def test_the_one_exception_only_when_the_person_left_test_builds_on_this_build(left):
    """Back on Releases from a test build - switched there by the person -
    the release that build carries the number of is offered too, so the
    channel means releases again; never as an offer made by itself, never
    an older release, never on Test builds, never for another build."""
    left("1.4.0.12")
    assert offered("v1.4.0", "1.4.0.12", "stable")
    assert offered("v1.4.1", "1.4.0.12", "stable")
    assert not offered("v1.3.9", "1.4.0.12", "stable")
    assert not offered("v1.4.0", "1.4.0.12", "nightly")
    assert not offered("v1.4.0.11", "1.4.0.12", "stable")       # a test build: newer only
    assert not offered("v1.4.0", "1.4.0.13", "stable"), "left on another build"
    assert not offered("v1.4.0", "1.4.0", "stable")              # a release is no test build
    assert offered("v1.4.0", "1.4.0.12", "stable", left=True)
    assert not offered("v1.4.0", "1.4.0.12", "stable", left=False)
    left("")
    assert not offered("v1.4.0", "1.4.0.12", "stable")


def test_every_installer_refuses_what_is_not_offered(left, monkeypatch):
    """selfupdate.refuse_older - the AppImage's, Windows' and the Mac's -
    holds to the same: strictly newer, the one exception only once left."""
    monkeypatch.setattr(selfupdate, "VERSION", "1.4.0.12")
    for tag in ("v1.4.0", "v1.3.0", "v1.4.0.12", "v1.4.0.11", ""):
        with pytest.raises(selfupdate.UpdateError):
            selfupdate.refuse_older(tag)
    selfupdate.refuse_older("v1.4.1")
    selfupdate.refuse_older("v1.4.0.13")
    left("1.4.0.12")
    selfupdate.refuse_older("v1.4.0")
    with pytest.raises(selfupdate.UpdateError):
        selfupdate.refuse_older("v1.3.0")


def test_the_window_notes_a_switch_from_test_builds_to_releases(left, tmp_path):
    from PyQt6.QtCore import QSettings

    from dopeiptv.ui.mw_updates import K_LEFT_NIGHTLY, channel_seen
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    # A first look, on Releases: nothing was left.
    channel_seen(s, "stable", "1.4.0.12")
    assert s.value(K_LEFT_NIGHTLY) is None and not updates.left_nightly("1.4.0.12")
    # On Test builds, then back on Releases while the test build runs.
    channel_seen(s, "nightly", "1.4.0.12")
    channel_seen(s, "stable", "1.4.0.12")
    assert s.value(K_LEFT_NIGHTLY) == "1.4.0.12" and updates.left_nightly("1.4.0.12")
    assert offered("v1.4.0", "1.4.0.12", "stable")
    # Kept over a restart of the same build; another build is not it.
    channel_seen(s, "stable", "1.4.0.12")
    assert updates.left_nightly("1.4.0.12")
    channel_seen(s, "stable", "1.4.1")
    assert not updates.left_nightly("1.4.1") and not offered("v1.4.1", "1.4.1", "stable")
    # Test builds again: forgotten.
    channel_seen(s, "nightly", "1.4.0.12")
    assert s.value(K_LEFT_NIGHTLY) is None and not updates.left_nightly("1.4.0.12")
    # A release's copy switched back: no test build to leave.
    channel_seen(s, "stable", "1.4.0")
    assert s.value(K_LEFT_NIGHTLY) is None


def test_a_test_build_comes_from_the_site_too_fetched_through_it(github):
    github["site"] = site_json("1.4.0")
    github["site_nightly"] = site_json("1.4.0.16", names=("x86_64.AppImage", "windows-x64-setup.exe"))
    github["all"] = [rel("nightly-1.4.0.15", prerelease=True)]          # GitHub's: not asked
    r = fetch_update(channel="nightly")
    assert r["tag"] == "v1.4.0.16" and r["nightly"] is True
    assert not any("api.github.com" in u for u in github["asked"])
    a = selfupdate.pick_asset(r["assets"], r["tag"], "x86_64")
    # /dl.php counts it and sends it on to the pre-release on GitHub.
    assert a["url"] == "https://dopeiptv.org/dl.php?f=dopeIPTV-1.4.0.16-x86_64.AppImage"


def test_without_the_sites_nightly_json_github_has_the_test_build(github):
    github["site"] = site_json("1.4.0")
    github["all"] = [rel("nightly-1.4.0.15", prerelease=True)]
    assert fetch_update(channel="nightly")["tag"] == "v1.4.0.15"
    github["site_nightly"] = site_json("1.4.0")                         # a release, not a test build
    assert fetch_update(channel="nightly")["tag"] == "v1.4.0.15"


def test_a_test_builds_download_is_this_computers_file_through_the_site(monkeypatch):
    # The app cannot update itself (a Mac copy outside Applications, a
    # portable Windows copy): "Download" fetches its file through
    # dopeiptv.org, counted, rather than opening GitHub's release page.
    import sys

    from dopeiptv.core import macupdate, winupdate
    from dopeiptv.ui import mw_updates
    site = "https://dopeiptv.org/dl.php?f="
    names = ["dopeIPTV-1.4.5.80-macOS-arm64.dmg", "dopeIPTV-1.4.5.80-macOS-x86_64.dmg",
             "dopeIPTV-1.4.5.80-windows-x64-setup.exe", "dopeIPTV-1.4.5.80-windows-x64.zip",
             "dopeIPTV-1.4.5.80-x86_64.AppImage"]
    rel = {"tag": "v1.4.5.80", "nightly": True, "assets": [{"name": n, "url": site + n} for n in names]}
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(macupdate, "arch", lambda: "arm64")
    assert mw_updates._own_file(rel) == site + "dopeIPTV-1.4.5.80-macOS-arm64.dmg"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(winupdate, "arch", lambda: "x64")
    monkeypatch.setattr(winupdate, "installed_dir", lambda: None)
    assert mw_updates._own_file(rel) == site + "dopeIPTV-1.4.5.80-windows-x64.zip"
    monkeypatch.setattr(winupdate, "installed_dir", lambda: object())
    assert mw_updates._own_file(rel) == site + "dopeIPTV-1.4.5.80-windows-x64-setup.exe"
    # Linux, and a build without this computer's file: its release page.
    monkeypatch.setattr(sys, "platform", "linux")
    assert mw_updates._own_file(rel) == ""
    monkeypatch.setattr(sys, "platform", "darwin")
    assert mw_updates._own_file({"tag": "v1.4.5.80", "assets": []}) == ""


class Moved(Resp):
    """A 302 with its Location (requests' is_redirect)."""

    is_redirect = True

    def __init__(self, location) -> None:
        super().__init__({})
        self.headers = {"Location": location}

    def close(self) -> None:
        pass


def test_the_site_s_answer_is_never_followed_elsewhere(github, monkeypatch):
    """Rule 2: dopeiptv.org's services go through build_check.secure and
    follow no redirect. One sent elsewhere is not taken - GitHub is asked,
    as when the site does not answer."""
    fake = updates.requests.get

    def get(url, **kw):
        if url == "https://dopeiptv.org/releases.json":
            github["asked"].append(url)
            return Moved("http://mirror.example/releases.json")
        return fake(url, **kw)
    monkeypatch.setattr(updates.requests, "get", get)
    assert fetch_update(channel="stable")["tag"] == "v1.4.0"
    assert "http://mirror.example/releases.json" not in github["asked"]
    monkeypatch.setattr(updates.requests, "get", fake)
    # An address that is not https to the site itself is not asked at all.
    github["asked"].clear()
    monkeypatch.setattr(updates, "SITE", "http://dopeiptv.org")
    assert fetch_update(channel="stable")["tag"] == "v1.4.0"
    assert not [u for u in github["asked"] if "dopeiptv.org" in u], github["asked"]


def test_github_s_redirects_are_followed_only_over_https(github, monkeypatch):
    """A moved repository's answer is followed, each hop https; plain http
    is refused before it is asked."""
    fake = updates.requests.get
    hops = {"https://api.github.com/repos/slimture/dopeIPTV/releases/latest":
            Moved("https://api.github.com/repositories/1/releases/latest"),
            "https://api.github.com/repositories/1/releases/latest": Resp(rel("v1.4.2"))}

    def get(url, **kw):
        github["asked"].append(url)
        return hops[url] if url in hops else fake(url, **kw)
    monkeypatch.setattr(updates.requests, "get", get)
    assert fetch_update(channel="stable")["tag"] == "v1.4.2"
    hops["https://api.github.com/repositories/1/releases/latest"] = Moved("http://evil.example/latest")
    with pytest.raises(selfupdate.UpdateError):
        fetch_update(channel="stable")
    assert "http://evil.example/latest" not in github["asked"]
