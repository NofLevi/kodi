"""Ktuvit, the one subtitle provider that needs an account.

Every other provider here is anonymous, so this one is the only place where a
session can go stale, a credential can be missing, or a switch can be on with
nothing behind it. Those are what these cover.

Ktuvit has never been reached with a real account. These run against the
documented flow, so what they verify is this add-on's behaviour, not Ktuvit's.
"""
import json

import pytest

from katan import http, settings
from katan.subs import auto
from katan.subs.providers import ktuvit

EMAIL = "someone@example.com"
PASSWORD = "hunter2"
COOKIE = "Login=abc123"


class Response(object):
    def __init__(self, payload=None, status=200, text="", headers=None):
        self._payload = payload
        self.status_code = status
        self.text = text
        self.headers = headers or {}
        self.cookies = {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


MOVIE_PAGE = (
    '<div class="row">'
    '<div data-subtitle-id="sub-1">'
    '<div class="subtitle-name">Show.S01E01.1080p.WEB.H264-AAA</div></div>'
    '<div data-subtitle-id="sub-2">'
    '<div class="subtitle-name">Show.S01E01.720p.HDTV.x264-BBB</div></div>'
    '</div>')


@pytest.fixture
def account(settings_module):
    settings_module.set_many({
        "subs.provider.ktuvit": "true",
        "subs.ktuvit.user": EMAIL,
        "subs.ktuvit.password": PASSWORD,
    })
    return settings_module


@pytest.fixture
def site(monkeypatch, account):
    calls = []
    state = {
        "films": [{"ID": "42", "ImdbID": "tt1234567", "Name": "Show"}],
        "download_id": "dl-99",
        "login_status": 200,
        "page": MOVIE_PAGE,
    }

    def fake_post(url, **kwargs):
        calls.append(url)
        if url == ktuvit.LOGIN:
            return Response(status=state["login_status"],
                            headers={"Set-Cookie": COOKIE + "; Path=/"})
        if url == ktuvit.SEARCH:
            return Response({"d": json.dumps({"Films": state["films"]})})
        if url == ktuvit.REQUEST_DOWNLOAD:
            return Response({"d": {"DownloadIdentifier": state["download_id"]}})
        return None

    def fake_get(url, **kwargs):
        calls.append(url)
        if "MovieInfo.aspx" in url:
            return Response(text=state["page"])
        return Response(text="")

    monkeypatch.setattr(http, "post", fake_post)
    monkeypatch.setattr(http, "get", fake_get)
    monkeypatch.setattr(ktuvit.common, "fetch_bytes",
                        lambda url, **kw: b"1\n00:00:01,000 --> 00:00:02,000\nx\n")
    return {"calls": calls, "state": state}


def meta():
    return {"type": "episode", "title": "Pilot", "show_title": "Show",
            "season": 1, "episode": 1, "ids": {"imdb": "tt1234567"}}


# --------------------------------------------------------------------------
# the account gate
# --------------------------------------------------------------------------


def test_nothing_happens_without_credentials(settings_module, no_network):
    settings_module.set_many({"subs.ktuvit.user": "", "subs.ktuvit.password": ""})
    assert ktuvit.configured() is False
    assert ktuvit.search(meta(), None, ["he"]) == []
    assert ktuvit.session_cookie() == ""


def test_it_only_offers_hebrew(account):
    assert ktuvit.supports("he") is True
    assert ktuvit.supports("en") is False


def test_a_non_hebrew_search_asks_for_nothing(site):
    assert ktuvit.search(meta(), None, ["en"]) == []
    assert not site["calls"]


def test_it_is_off_by_default():
    """A provider that is on but cannot sign in silently returns nothing."""
    assert settings.DEFAULTS["subs.provider.ktuvit"] == "false"


# --------------------------------------------------------------------------
# the session
# --------------------------------------------------------------------------


def test_the_password_is_hashed_on_the_wire(site, monkeypatch):
    """Not a security claim - it is simply the format the site expects."""
    sent = {}

    def capture(url, **kwargs):
        if url == ktuvit.LOGIN:
            sent.update((kwargs.get("json") or {}).get("request") or {})
            return Response(headers={"Set-Cookie": COOKIE})
        return Response({"d": json.dumps({"Films": []})})

    monkeypatch.setattr(http, "post", capture)
    ktuvit.session_cookie(refresh=True)
    assert sent["Email"] == EMAIL
    assert sent["Password"] != PASSWORD
    assert len(sent["Password"]) == 40, "expected a sha1 hex digest"


def test_the_session_is_cached(site):
    ktuvit.session_cookie()
    ktuvit.session_cookie()
    assert len([c for c in site["calls"] if c == ktuvit.LOGIN]) == 1


def test_one_search_costs_one_login(site):
    ktuvit.search(meta(), None, ["he"])
    ktuvit.search(meta(), None, ["he"])
    assert len([c for c in site["calls"] if c == ktuvit.LOGIN]) == 1


def test_a_refused_login_yields_no_cookie(site):
    site["state"]["login_status"] = 401
    assert ktuvit.session_cookie(refresh=True) == ""


def test_a_stale_session_is_re_established(monkeypatch, account):
    """A cookie cached for a day will sometimes be dead when it is used."""
    attempts = {"login": 0, "search": 0}

    def fake_post(url, **kwargs):
        if url == ktuvit.LOGIN:
            attempts["login"] += 1
            return Response(headers={"Set-Cookie": COOKIE})
        if url == ktuvit.SEARCH:
            attempts["search"] += 1
            if attempts["search"] == 1:
                return Response(status=401)
            return Response({"d": json.dumps({"Films": []})})
        return None

    monkeypatch.setattr(http, "post", fake_post)
    monkeypatch.setattr(http, "get", lambda url, **kw: Response(text=""))
    ktuvit.search(meta(), None, ["he"])
    assert attempts["login"] == 2, "a 401 should trigger exactly one new login"
    assert attempts["search"] == 2


# --------------------------------------------------------------------------
# searching and downloading
# --------------------------------------------------------------------------


def test_versions_are_returned_with_their_release_names(site):
    found = ktuvit.search(meta(), None, ["he"])
    assert [c["release"] for c in found] == [
        "Show.S01E01.1080p.WEB.H264-AAA", "Show.S01E01.720p.HDTV.x264-BBB"]
    assert all(c["provider"] == "ktuvit" for c in found)
    assert all(c["language"] == "he" for c in found)


def test_the_imdb_id_picks_the_right_title(site):
    site["state"]["films"] = [
        {"ID": "1", "ImdbID": "tt0000000", "Name": "Wrong"},
        {"ID": "42", "ImdbID": "tt1234567", "Name": "Right"},
    ]
    found = ktuvit.search(meta(), None, ["he"])
    assert found and found[0]["download"].startswith("42|")


def test_explicit_imdb_mismatch_yields_nothing(site):
    site["state"]["films"] = [
        {"ID": "1", "ImdbID": "tt9999999", "Name": "Wrong"},
        {"ID": "2", "ImdbID": "8888888", "Name": "Also Wrong"},
    ]
    assert ktuvit.search(meta(), None, ["he"]) == []


def test_no_matching_title_yields_nothing(site):
    site["state"]["films"] = []
    assert ktuvit.search(meta(), None, ["he"]) == []


def test_a_page_with_no_subtitles_yields_nothing(site):
    site["state"]["page"] = "<html>nothing here</html>"
    assert ktuvit.search(meta(), None, ["he"]) == []


def test_downloading_asks_for_an_identifier_first(site):
    found = ktuvit.search(meta(), None, ["he"])
    data = ktuvit.download(found[0])
    assert data.startswith(b"1\n")
    assert ktuvit.REQUEST_DOWNLOAD in site["calls"]


def test_a_refused_download_identifier_yields_nothing(site):
    found = ktuvit.search(meta(), None, ["he"])
    site["state"]["download_id"] = ""
    assert ktuvit.download(found[0]) == b""


def test_a_malformed_reference_yields_nothing(site):
    assert ktuvit.download({"download": "no-separator"}) == b""
    assert ktuvit.download({}) == b""


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------


def test_ktuvit_is_registered_as_a_provider():
    assert "ktuvit" in auto._modules()


def test_it_is_asked_after_the_faster_hebrew_sources(account, settings_module):
    """Ktuvit has the best catalogue and the only sign-in, so it is not first."""
    settings_module.set_many({"subs.provider.wizdom": "true",
                              "subs.provider.ktuvit": "true"})
    names = [name for name, _module in auto._providers()]
    assert "ktuvit" in names
    assert names.index("wizdom") < names.index("ktuvit")


def test_the_settings_have_defaults():
    for key in ("subs.provider.ktuvit", "subs.ktuvit.user",
                "subs.ktuvit.password"):
        assert key in settings.DEFAULTS
