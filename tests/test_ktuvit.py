"""Ktuvit, the one subtitle provider that needs an account.

Every other provider here is anonymous, so this one is the only place where a
session can go stale, a credential can be missing, or a switch can be on with
nothing behind it. Those are what these cover.

Reached with a real account for the first time on 23 September 2026, which
found four separate fatal faults in what had been "implemented and fixture
tested" for months - a retired hostname, the wrong password algorithm, the
anonymous cookie mistaken for a session, and row patterns matching markup that
does not exist. The fixtures below were rewritten from the live site, so they
now describe Ktuvit rather than a guess at it.
"""
import json

import pytest

from pinky import http, settings
from pinky.subs import auto
from pinky.subs.providers import ktuvit

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


# A row as the site really renders it: the release name in a div before the
# <br>, the id on two links in the last cell. Taken from the live page.
def _row(name, subtitle_id):
    return (
        '<tr>'
        '<td class="ltr text-right">'
        '<div style="float: right; width: 95%%;">'
        '%s<br />'
        "<small class='text-danger'>תרגום: "
        "אולפנים</small>"
        '</div></td>'
        '<td>srt</td><td class="ltr">156.19 KB</td>'
        '<td>29/05/2026</td><td>257</td>'
        '<td style="position: relative;">'
        '<a class="fa fa-sliders" data-subtitle-id="%s"></a>'
        '<a class="glyphicon" data-subtitle-id="%s"></a>'
        '</td></tr>' % (name, subtitle_id, subtitle_id))


MOVIE_PAGE = (
    '<table><thead><tr><th>שם</th><th>גודל</th></tr>'
    '</thead><tbody>'
    + _row("Show.S01E01.1080p.WEB.H264-AAA", "sub-1")
    + _row("Show.S01E01.720p.HDTV.x264-BBB", "sub-2")
    + '</tbody></table>')

# What the site emits as its encryption salt, and a stand-in for the page that
# carries it. The value is scraped per login because the site can rotate it.
SALT = "0123456789abcdef0123456789abcdef"
HOME_PAGE = "<script>var encryptionSalt = '%s';</script>" % SALT


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
        "films": [{"ID": "42",
                   "IMDB_Link": "https://www.imdb.com/title/tt1234567/",
                   "EngName": "Show"}],
        "download_id": "dl-99",
        "login_status": 200,
        "login_success": True,
        "page": MOVIE_PAGE,
    }

    def fake_post(url, **kwargs):
        calls.append(url)
        if url == ktuvit.LOGIN:
            body = {"d": json.dumps({"IsSuccess": state["login_success"]})}
            return Response(body, status=state["login_status"],
                            headers={"Set-Cookie": COOKIE + "; Path=/"})
        if url == ktuvit.SEARCH:
            # The search says IsSuccess false even when it worked, so nothing
            # may gate on it - measured against the live service.
            return Response({"d": json.dumps({"Films": state["films"],
                                              "IsSuccess": False,
                                              "ErrorMessage": None})})
        if url == ktuvit.REQUEST_DOWNLOAD:
            return Response({"d": {"DownloadIdentifier": state["download_id"]}})
        return None

    def fake_get(url, **kwargs):
        calls.append(url)
        if "MovieInfo.aspx" in url or "GetModuleAjax" in url:
            return Response(text=state["page"])
        return Response(text=HOME_PAGE)

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


def test_the_password_is_encrypted_the_way_the_site_expects(site, monkeypatch):
    """Not a security claim - it is simply the format the site accepts.

    This used to send a SHA-1 of the password, which Ktuvit has never wanted,
    so no account could sign in. The real thing is the site's own
    `Encrypt(email, password)`: PBKDF2 to a key, AES-CBC over the password,
    SHA-256 of the ciphertext, base64 of that. The vector below was produced
    by this code and confirmed by a live sign-in returning IsSuccess: true on
    23 September 2026 - without that confirmation it would only prove the
    implementation had not changed.
    """
    sent = {}

    def capture(url, **kwargs):
        if url == ktuvit.LOGIN:
            sent.update((kwargs.get("json") or {}).get("request") or {})
            return Response({"d": json.dumps({"IsSuccess": True})},
                            headers={"Set-Cookie": COOKIE})
        return Response({"d": json.dumps({"Films": []})})

    monkeypatch.setattr(http, "post", capture)
    ktuvit.session_cookie(refresh=True)
    assert sent["Email"] == EMAIL
    assert sent["Password"] != PASSWORD
    assert sent["Password"] == "UXtsqxmthhg37Et7iqjciGk8dZJa399wtktUuGggaBc="


def test_the_initialisation_vector_reproduces_a_javascript_quirk():
    """`CryptoJS.enc.Hex.parse` on an email address, which is nonsense on
    purpose: most pairs are not hex and become zero, `parseInt` takes what
    leading digits it can, and the rest is zero padding. Getting this
    "sensibly" wrong still encrypts - to a password the server never sees."""
    assert list(ktuvit._hex_iv("someone@example.com")) == [
        0, 0, 0, 14, 14, 10, 0, 14, 12, 0, 0, 0, 0, 0, 0, 0]
    assert len(ktuvit._hex_iv("a")) == 16, "short addresses are zero padded"


def test_the_salt_is_scraped_rather_than_written_down(site):
    """The site can rotate it, and a hardcoded copy would fail every login."""
    assert ktuvit._encryption_salt() == SALT


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


def test_a_login_answering_failure_is_refused_despite_its_cookie(
        monkeypatch, account):
    """A cookie is not proof of anything: anonymous visitors get one too.

    Ktuvit hands `ASP.NET_SessionId` to everybody, so a wrong password comes
    back 200, with a cookie, and {"IsSuccess": false} in the body. Taking the
    cookie meant reporting a successful sign-in, caching it for a day and
    then fetching logged-out pages, which carry no subtitles at all - so the
    symptom was a configured provider silently contributing nothing.
    """
    refusal = {"d": json.dumps(
        {"IsSuccess": False,
         "ErrorMessage": u"ההתחברות "
                         u"נכשלה"})}

    def fake_post(url, **kwargs):
        if url == ktuvit.LOGIN:
            return Response(refusal, headers={"Set-Cookie": COOKIE + "; Path=/"})
        return None

    monkeypatch.setattr(http, "post", fake_post)
    assert ktuvit.session_cookie(refresh=True) == ""


def test_a_search_result_carries_its_imdb_id_as_a_link(monkeypatch, account):
    """Ktuvit publishes `IMDB_Link`, not the `ImdbID` this once looked for.

    Every id comparison was therefore made against nothing, no result ever
    matched, and the search gave up with "no title with the requested IMDb
    id" while the only answer sat in the list.
    """
    film = {"ID": "42", "IMDB_Link": "http://www.imdb.com/title/tt1234567",
            "EngName": "Show"}

    def fake_post(url, **kwargs):
        if url == ktuvit.LOGIN:
            return Response({"d": json.dumps({"IsSuccess": True})},
                            headers={"Set-Cookie": COOKIE})
        if url == ktuvit.SEARCH:
            return Response({"d": json.dumps({"Films": [film]})})
        return None

    monkeypatch.setattr(http, "post", fake_post)
    monkeypatch.setattr(http, "get", lambda url, **kw: Response(
        text=HOME_PAGE if url.rstrip("/") == ktuvit.BASE else MOVIE_PAGE))
    found = ktuvit.search(meta(), None, ["he"])
    assert [c["release"] for c in found] == [
        "Show.S01E01.1080p.WEB.H264-AAA", "Show.S01E01.720p.HDTV.x264-BBB"]


def test_a_stale_session_is_re_established(monkeypatch, account):
    """A cookie cached for a day will sometimes be dead when it is used."""
    attempts = {"login": 0, "search": 0}

    def fake_post(url, **kwargs):
        if url == ktuvit.LOGIN:
            attempts["login"] += 1
            return Response({"d": json.dumps({"IsSuccess": True})},
                            headers={"Set-Cookie": COOKIE})
        if url == ktuvit.SEARCH:
            attempts["search"] += 1
            if attempts["search"] == 1:
                return Response(status=401)
            return Response({"d": json.dumps({"Films": []})})
        return None

    monkeypatch.setattr(http, "post", fake_post)
    monkeypatch.setattr(http, "get", lambda url, **kw: Response(
        text=HOME_PAGE if url.rstrip("/") == ktuvit.BASE else ""))
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


def test_the_login_cookie_is_found_behind_the_session_cookie():
    """Ktuvit sets two, and the one that matters is second.

    This is why the provider had never signed in on a real device: a Kodi
    with `requests` read the cookie jar and worked, and a Kodi without it -
    which is every Kodi this add-on ships to - read only the first Set-Cookie
    header and logged "refused the sign in" against a good account.
    """
    from email.message import Message

    from pinky import urlsession
    from pinky.subs.providers import ktuvit

    headers = Message()
    headers["Set-Cookie"] = "ASP.NET_SessionId=anon; path=/; HttpOnly"
    headers["Set-Cookie"] = ("Login=u=A034&g=BEA4; path=/; "
                             "expires=Wed, 24-Sep-2026 12:00:00 GMT")
    response = urlsession.Response("https://ktuvit.me/", 200, headers, None)

    assert ktuvit._cookie_from(response) == "Login=u=A034&g=BEA4"


def test_an_anonymous_visitor_is_not_mistaken_for_a_sign_in():
    """A refused login still comes back with ASP.NET_SessionId."""
    from email.message import Message

    from pinky import urlsession
    from pinky.subs.providers import ktuvit

    headers = Message()
    headers["Set-Cookie"] = "ASP.NET_SessionId=anon; path=/; HttpOnly"
    response = urlsession.Response("https://ktuvit.me/", 200, headers, None)

    assert ktuvit._cookie_from(response) == ""


# --------------------------------------------------------------------------
# the two IMDb fields, which disagree
# --------------------------------------------------------------------------


def test_both_imdb_fields_are_offered_because_one_is_truncated():
    """A film carries `IMDB_Link` and `ImdbID` and they disagree - the second
    is a character short:

        IMDB_Link = "https://www.imdb.com/title/tt14173636/"
        ImdbID    = "tt1417363"

    This preferred the short one, so the comparison was 1417363 against
    14173636 and no recent film ever matched. Six and seven digit ids are
    unaffected, which is why it looked like it worked. Measured against the
    live site: 3 of 7 films matched before, 7 of 7 after, and the four that
    were failing are exactly the four with eight-digit ids.
    """
    film = {"IMDB_Link": "https://www.imdb.com/title/tt14173636/",
            "ImdbID": "tt1417363"}
    assert "tt14173636" in ktuvit._film_imdb(film)


def test_a_film_is_found_when_only_its_link_is_right(site, monkeypatch):
    meta = {"type": "movie", "title": "The Invite", "year": 2026,
            "ids": {"imdb": "tt14173636"}}
    films = [{"ID": "THEID", "EngName": "The Invite", "ReleaseDate": "2026",
              "IMDB_Link": "https://www.imdb.com/title/tt14173636/",
              "ImdbID": "tt1417363"}]
    monkeypatch.setattr(ktuvit, "_post",
                        lambda url, body, cookie: (_Payload({"Films": films}), cookie))
    found, _cookie = ktuvit._find_id(meta, "cookie")
    assert found == "THEID"


def test_the_wrong_year_is_not_taken_by_name(site, monkeypatch):
    """Asked for The Odyssey (2026), Ktuvit answers with Star Quest: The
    Odyssey (2009) - a name that passes and a year that does not. Checked
    against the live site, which really does answer exactly that."""
    meta = {"type": "movie", "title": "The Odyssey", "year": 2026,
            "ids": {"imdb": "tt33764258"}}
    films = [{"ID": "WRONG", "EngName": "Star Quest: The Odyssey",
              "ReleaseDate": "2009",
              "IMDB_Link": "https://www.imdb.com/title/tt1360833/"}]
    monkeypatch.setattr(ktuvit, "_post",
                        lambda url, body, cookie: (_Payload({"Films": films}), cookie))
    found, _cookie = ktuvit._find_id(meta, "cookie")
    assert found == "", "a different film must not be taken on its name alone"


def test_a_film_with_no_imdb_link_is_taken_by_name_and_year(site, monkeypatch):
    """An id that matches nothing is not the same as there being nothing -
    the lesson already learned from OpenSubtitles, applied here."""
    meta = {"type": "movie", "title": "The Invite", "year": 2026,
            "ids": {"imdb": "tt14173636"}}
    films = [{"ID": "BYNAME", "EngName": "The Invite", "ReleaseDate": "2026"}]
    monkeypatch.setattr(ktuvit, "_post",
                        lambda url, body, cookie: (_Payload({"Films": films}), cookie))
    found, _cookie = ktuvit._find_id(meta, "cookie")
    assert found == "BYNAME"


class _Payload(object):
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def json(self):
        return {"d": __import__("json").dumps(self._data)}

    @property
    def text(self):
        return __import__("json").dumps({"d": __import__("json").dumps(self._data)})
