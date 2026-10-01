"""Ktuvit, the largest Hebrew subtitle source, which needs an account.

Every other provider here is anonymous. Ktuvit is not: it is a members' site,
and nothing at all comes back without a signed-in session. That is why it was
left out of the first release rather than shipped as a switch that did nothing.

The flow:

    /                                    scrape `encryptionSalt`
    MembershipService.svc/Login          email and an encrypted password, in
                                         exchange for a `Login` cookie
    ContentProvider.svc/SearchPage_search   find the film or series
    MovieInfo.aspx?ID=...                a film's subtitle versions
    GetModuleAjax.ashx?moduleName=SubtitlesList
                                         an episode's, which is a different
                                         endpoint and not a parameter
    RequestSubtitleDownload + DownloadFile.ashx
                                         ask for a download id, then use it

**This was signed in to a real account for the first time on 23 September
2026, and almost nothing about it was right.** It had been written from a
guess at the site rather than from the site, shipped off by default, and so
never contradicted: no unit test reaches the network, and nobody without an
account would ever see a log line. Four independent faults, any one of which
was fatal on its own - the host was NXDOMAIN, the password was the wrong
algorithm, the cookie was the anonymous one, and both row regexes matched
markup that does not exist. Worth remembering the next time something ships
"implemented and fixture tested".

The password is not hashed, it is **encrypted**, by an algorithm that only
makes sense as an accident of the site's own JavaScript - PBKDF2 to a key,
AES-CBC over the password, SHA-256 of the ciphertext, base64 of that. It is
not a security property and this add-on claims none: it is simply the wire
format, and `_encrypt_password` reproduces it, quirks and all. The password
is stored in Kodi's settings like every other credential here.

The salt that keys it is scraped from the homepage on each login, not written
down, because the site can rotate it.

The session cookie is cached and reused. Logging in per search would be both
slow and rude to a small community site, so a session is kept for a day and
re-established only when a request comes back looking signed out - which,
awkwardly, is an HTTP 200 carrying a sentence of Hebrew.

Most of the site is readable anonymously: search, the episode list, even
issuing a download identifier. Only a film's subtitle table and the file
itself need the cookie, so a successful identifier proves nothing about the
session and the failure lands on the fetch.
"""
import base64
import hashlib
import re
import time

from ... import cache, http, kodi, settings
from ...utils import aes
from . import common

NAME = "ktuvit"
# The apex domain, not `members.` - that hostname was retired and is now
# NXDOMAIN, so every request this provider made failed before it was sent and
# no credentials could ever have worked. Measured 23 September 2026: the same
# paths on ktuvit.me answer, and an empty login returns the documented
# {"d": "{\"IsSuccess\":false,...}"} envelope.
BASE = "https://ktuvit.me"
LANGUAGE = "he"

LOGIN = BASE + "/Services/MembershipService.svc/Login"
SEARCH = BASE + "/Services/ContentProvider.svc/SearchPage_search"
MOVIE_INFO = BASE + "/MovieInfo.aspx?ID=%s"
# Series do not use MovieInfo.aspx, which ignores season and episode.
EPISODE_LIST = (BASE + "/Services/GetModuleAjax.ashx?moduleName=SubtitlesList"
                "&SeriesID=%s&Season=%d&Episode=%d")
REQUEST_DOWNLOAD = BASE + "/Services/ContentProvider.svc/RequestSubtitleDownload"
DOWNLOAD = BASE + "/Services/DownloadFile.ashx?DownloadIdentifier=%s"

# Every other provider caps what it hands back - OpenSubtitles at 12 per
# language, BSPlayer at 20 - and this one did not. A film with hundreds of
# versions therefore put hundreds of candidates into `outlook`, which weighs
# *every* candidate against *every* source when the picker opens. Measured:
# Ktuvit answered Pulp Fiction with 1081, which alone turns that pass into
# about a million comparisons and thrashes the release-name parse cache.
MAX_RESULTS = 40

SESSION_TTL = 24 * 3600

# How long a *refused* login is remembered. A refusal is a wrong password or a
# closed account, and neither becomes right in the next few minutes - but this
# used to remember only successes, so every search scraped the homepage for the
# salt and posted the login again. Measured in a real Kodi with a bad password:
# `ktuvit refused the sign in` once per search and `deadline hit after 10.0s,
# dropped: ktuvit`, so one of four workers was spending the *whole* deadline on
# a provider that could not answer, and whatever was behind it in the queue was
# dropped with it. Much shorter than a success, because the fix for a refusal
# is somebody typing the password again and they should not have to wait a day
# to find out it worked.
REFUSAL_TTL = 15 * 60
_REFUSED = "refused"
TIMEOUT = (5, 12)


# Where the session lives between searches. It was this process's memory
# alone, and "one login a day" was true only of the background service: Kodi
# runs every press as a new Python, so the picker scraped the homepage for
# the salt and signed in again on *every* search. Measured in a real Kodi,
# Ktuvit answered at ten seconds both times - once just inside the deadline
# and once dropped by it - where the search itself takes one. Over 239
# episodes it is the only Hebrew source for eight and carries a release name
# nobody else has on 102, so losing it loses rows and, more often, the fit.
#
# A window property is Kodi's own memory: every invocation reads it, nothing
# is written to storage - the reason this was never in the SQLite cache -
# and it is gone when Kodi exits.
def _held(key):
    value = cache.volatile_get(key)
    if value:
        return value
    expires, _, value = (kodi.get_property(key) or "").partition("|")
    try:
        remaining = float(expires) - time.time()
    except ValueError:
        return None
    if not value or remaining <= 0:
        return None
    return cache.volatile_set(key, value, remaining)


def _hold(key, value, ttl):
    kodi.set_property(key, "%d|%s" % (time.time() + ttl, value))
    return cache.volatile_set(key, value, ttl)


HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
    "Referer": BASE + "/",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
}

# One subtitle per table row, in both the film page and the episode fragment -
# they render the same six columns, which is why one parser serves both.
#
# The two regexes that used to be here matched nothing at all: there is no
# `subtitle-name` class in the markup and no `downloadSubtitle(` anywhere, so
# every signed-in search returned an empty list. Neither could have been
# written from the real page; they were written from a guess at it.
#
# What is actually there, measured against a live account on 23 September 2026:
#
#     <tr>
#       <td class="ltr text-right">
#         <div style="float: right; width: 95%;">
#           Pulp.Fiction.1994.2160p.4K.BluRay.x265.10bit.AAC5.1-[YTS.MX]<br />
#           <small>תרגום: אולפנים</small> ... credits ...
#       <td>srt</td><td>156.19 KB</td><td>29/05/2026</td><td>257</td>
#       <td><a data-subtitle-id="0104C18D..."></a><a data-subtitle-id="..."></a>
#
# The id appears twice per row, once for the styled download and once for the
# plain one, and they are the same value.
_ROWS = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_ROW_NAME = re.compile(r"<div[^>]*>\s*([^<]+?)\s*<br", re.S | re.I)
_ROW_ID = re.compile(r'data-subtitle-id="([^"]+)"', re.I)


def credentials():
    return (settings.get("subs.ktuvit.user", "").strip(),
            settings.get("subs.ktuvit.password", "").strip())


def configured():
    email, password = credentials()
    return bool(email and password)


def supports(language):
    return language == LANGUAGE


# --------------------------------------------------------------------------
# the session
# --------------------------------------------------------------------------


_SALT = re.compile(r"var\s+encryptionSalt\s*=\s*'([^']+)'")

# The salt is scraped rather than written down, because the site can rotate it
# and a hardcoded copy would fail every login the day it did.
_SALT_TTL = 12 * 3600


def _encryption_salt():
    key = cache.make_key("subs", "ktuvit", "salt")
    # On disk, unlike the session: it is published on the site's front page,
    # and that page took eight seconds to arrive when it was timed - most of
    # the search's whole deadline, spent before the login had been sent.
    held = _held(key) or cache.get(key)
    if held:
        return held
    response = http.get(BASE + "/", headers=HEADERS, timeout=TIMEOUT)
    if response is None or response.status_code >= 400:
        return ""
    found = _SALT.search(response.text or "")
    if not found:
        kodi.log("ktuvit did not publish an encryption salt")
        return ""
    _hold(key, found.group(1), _SALT_TTL)
    cache.set(key, found.group(1), _SALT_TTL)
    return found.group(1)


def _hex_iv(email):
    """`CryptoJS.enc.Hex.parse(email)`, quirks and all.

    It reads the address two characters at a time as hexadecimal, and this is
    not a sane thing to do to an email address - which is the point. Most
    pairs are not hex, and `parseInt` answers NaN, which JavaScript turns into
    0 when it is shifted into a word. `parseInt` is also lenient, so "di"
    contributes 0x0d rather than nothing: it takes the leading digits it can
    and stops. Anything past the address is zero, because CryptoJS reads four
    words whether or not they were written.

    Reproducing the quirk exactly is the whole job. A "tidier" IV - the raw
    bytes of the address, say - encrypts perfectly and produces a password the
    server has never seen.
    """
    out = bytearray()
    for index in range(0, len(email), 2):
        digits = ""
        for char in email[index:index + 2]:
            if char in "0123456789abcdefABCDEF":
                digits += char
            else:
                break
        out.append(int(digits, 16) & 0xFF if digits else 0)
    return bytes(out[:16]) + b"\x00" * max(0, 16 - len(out))


def _encrypt_password(email, password, salt):
    """What Ktuvit's `Encrypt(email, password)` produces, in Python.

    Read out of the site's own `js/Modules/LoginHandler.js`:

        iv   = CryptoJS.enc.Hex.parse(email)
        key  = CryptoJS.PBKDF2(encryptionSalt, email, {keySize: 4, iterations: 3000})
        out  = SHA256(Base64.parse(AES.encrypt(password, key, {iv, CBC, Pkcs7})))
               .toString(Base64)

    Two things in there are easy to get wrong. `PBKDF2` takes the *salt* as
    its password and the *email* as its salt, which is backwards from how it
    reads, and its default hash is SHA-1. And `cipher.toString()` is the
    OpenSSL formatter, which prefixes "Salted__" only when CryptoJS generated
    the salt itself - here a key is supplied, so there is none, and the round
    trip through base64 leaves exactly the ciphertext.

    This replaces a plain SHA-1 of the password, which is what this provider
    sent for as long as it has existed. No account could ever have signed in.
    """
    key = hashlib.pbkdf2_hmac("sha1", salt.encode("utf-8"),
                              email.encode("utf-8"), 3000, 16)
    ciphertext = aes.encrypt_cbc(password.encode("utf-8"), key, _hex_iv(email))
    return base64.b64encode(hashlib.sha256(ciphertext).digest()).decode("ascii")


def session_cookie(refresh=False):
    """A signed-in cookie, cached so a search does not mean a login."""
    if not configured():
        return ""

    email, password = credentials()
    account = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]
    key = cache.make_key("subs", "ktuvit", "session", account)
    if not refresh:
        cached = _held(key)
        if cached:
            return "" if cached == _REFUSED else cached

    salt = _encryption_salt()
    if not salt:
        return ""
    response = http.post(
        LOGIN,
        json={"request": {"Email": email,
                          "Password": _encrypt_password(email, password, salt)}},
        headers=HEADERS, timeout=TIMEOUT)

    if response is None or response.status_code >= 400:
        # Not remembered: the site being down is not the account being wrong,
        # and it may answer on the next search.
        kodi.log("ktuvit login did not answer")
        return ""

    # The answer has to be read, not just the cookie taken. Ktuvit hands
    # `ASP.NET_SessionId` to anonymous visitors as well, so a *refused* login
    # still comes back with a cookie - and taking it meant this reported a
    # successful sign-in, cached it for a day, and then quietly fetched
    # logged-out pages that carry no subtitles at all. Measured against a real
    # account: a wrong password returns 200 with
    # {"IsSuccess": false, "ErrorMessage": "ההתחברות נכשלה"} and a cookie.
    answer = _json_of(response)
    if isinstance(answer, dict) and not answer.get("IsSuccess"):
        kodi.log("ktuvit refused the sign in: %s"
                 % (answer.get("ErrorMessage") or "no reason given"))
        _hold(key, _REFUSED, REFUSAL_TTL)
        return ""

    cookie = _cookie_from(response)
    if not cookie:
        kodi.log("ktuvit refused the sign in")
        _hold(key, _REFUSED, REFUSAL_TTL)
        return ""

    _hold(key, cookie, SESSION_TTL)
    return cookie


def _cookie_from(response):
    """The signed-in cookie, which is `Login` and nothing else.

    It used to accept `ASP.NET_SessionId`, and fall back to whatever the first
    Set-Cookie header happened to be. Both are wrong in the same way: Ktuvit
    hands a session id to anonymous visitors, so a *refused* login still
    produced a cookie, which was then cached for a day and used to fetch
    logged-out pages. Those pages carry no subtitle table at all, so the
    symptom was a configured provider that silently found nothing.

    The real one is `Login=u=<hex>&g=<hex>`, measured against a live account
    on 23 September 2026. `.AspNet.ApplicationCookie` does not exist here.
    """
    jar = getattr(response, "cookies", None)
    if jar:
        try:
            value = jar.get("Login")
            if value:
                return "Login=%s" % value
        except Exception:
            pass

    # Every Set-Cookie header, not the first one. This is where it has been
    # failing on every device: `urlsession` exposes headers as an
    # `email.message.Message`, whose `.get()` returns only the *first* header
    # of a repeated name - and Ktuvit sends `ASP.NET_SessionId` first and
    # `Login` second. So a Kodi with `requests` installed read the cookie jar
    # above and signed in, and a Kodi without it - which is every Kodi this
    # add-on ships to - saw the session id, found no `Login=`, and logged
    # "ktuvit refused the sign in" against a perfectly good account. The same
    # shape as the gzip header bug in `urlsession._case_insensitive`.
    headers = response.headers or {}
    get_all = getattr(headers, "get_all", None)
    raw = get_all("Set-Cookie") if get_all is not None else None
    if raw is None:
        single = headers.get("Set-Cookie", "")
        raw = [single] if single else []

    # Split on both separators. A folded header joins cookies with commas,
    # and `expires=Wed, 24-Sep-2026 ...` puts a comma inside one - so the
    # answer is to look at every token rather than to trust either split.
    for header in raw:
        for chunk in str(header).split(";"):
            for part in chunk.split(","):
                part = part.strip()
                if part.startswith("Login="):
                    return part
    return ""


def _signed_headers(cookie):
    headers = dict(HEADERS)
    headers["Cookie"] = cookie
    return headers


def _post(url, payload, cookie):
    """One signed call, re-establishing the session once if it looks stale."""
    response = http.post(url, json=payload, headers=_signed_headers(cookie),
                         timeout=TIMEOUT)
    if response is not None and response.status_code in (401, 403):
        cookie = session_cookie(refresh=True)
        if not cookie:
            return None, ""
        response = http.post(url, json=payload,
                             headers=_signed_headers(cookie), timeout=TIMEOUT)
    return response, cookie


def _json_of(response):
    if response is None or response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except Exception:
        return None
    # The service wraps everything in a d field, sometimes as a JSON string.
    inner = payload.get("d") if isinstance(payload, dict) else payload
    if isinstance(inner, str):
        import json
        try:
            inner = json.loads(inner)
        except ValueError:
            return None
    return inner


# --------------------------------------------------------------------------
# searching
# --------------------------------------------------------------------------


def search(meta, target, languages):
    if LANGUAGE not in languages or not configured():
        return []

    cookie = session_cookie()
    if not cookie:
        return []

    film_id, cookie = _find_id(meta, cookie)
    if not film_id:
        return []

    return _versions(meta, film_id, cookie)


def _film_imdb(film):
    """Every IMDb id a search result claims, because it claims two.

    A film carries both `IMDB_Link` and `ImdbID`, and **they disagree**:

        IMDB_Link = "https://www.imdb.com/title/tt14173636/"
        ImdbID    = "tt1417363"

    The second is a character short. This preferred it, so the comparison was
    `1417363` against `14173636` and no recent film ever matched - the search
    gave up with "no title with the requested IMDb id" while the film sat in
    the list as the only answer. Seven-digit ids are unaffected, which is why
    it looked like it worked: it fails for everything with an eight-digit id,
    which is everything made in the last decade.

    So both are returned and either may match. The link is the one to trust,
    but a field that is right most of the time is still worth asking.
    """
    ids = []
    found = re.search(r"tt\d+", str(film.get("IMDB_Link") or ""))
    if found:
        ids.append(found.group(0))
    for key in ("ImdbID", "IMDB_ID"):
        value = str(film.get(key) or "").strip()
        if value:
            ids.append(value)
    return ids


def _find_id(meta, cookie):
    """Ktuvit's own id for this title, found by IMDb id or by name."""
    ids = meta.get("ids") or {}
    is_episode = meta.get("type") == "episode"

    query = {
        "FilmName": meta.get("show_title") or meta.get("title") or "",
        "Actors": [], "Studios": None, "Directors": [], "Genres": [],
        "Countries": [], "Languages": [], "Year": "", "Rating": [],
        "Page": 1, "SearchType": "1" if is_episode else "0",
        "WithSubsOnly": False,
    }
    if ids.get("imdb"):
        query["ImdbID"] = ids["imdb"]

    response, cookie = _post(SEARCH, {"request": query}, cookie)
    payload = _json_of(response)
    if not isinstance(payload, dict):
        kodi.log("ktuvit search did not answer")
        return "", cookie

    films = payload.get("Films") or payload.get("films") or []
    if not isinstance(films, list) or not films:
        return "", cookie

    def imdb_id(value):
        value = str(value or "").strip().lower()
        if value.startswith("tt"):
            value = value[2:]
        return (value.lstrip("0") or "0") if value.isdigit() else ""

    wanted = imdb_id(ids.get("imdb"))
    films = [film for film in films if isinstance(film, dict)]
    for film in films:
        if wanted and wanted in [imdb_id(value) for value in _film_imdb(film)]:
            return str(film.get("ID") or ""), cookie

    # An id that matches nothing is not the same as there being nothing - the
    # lesson this add-on already learned from OpenSubtitles. Ktuvit may hold
    # the film with no IMDb link at all, and the search found it *by name*.
    #
    # The year is what makes that safe rather than reckless: asked for The
    # Odyssey (2026), Ktuvit answers with Star Quest: The Odyssey (2009),
    # whose name passes and whose year does not.
    named = _by_name(films, meta)
    if named:
        kodi.log("ktuvit had no IMDb match, taking %r by name and year"
                 % str(named.get("EngName") or "")[:40])
        return str(named.get("ID") or ""), cookie
    if wanted:
        kodi.log("ktuvit returned no title with the requested IMDb id")
        return "", cookie

    first = films[0] if films else {}
    return str(first.get("ID") or ""), cookie


def _by_name(films, meta):
    """A search result whose name *and* year both match what is playing."""
    from ...utils import release

    year = int(meta.get("year") or 0)
    title = meta.get("show_title") or meta.get("title") or ""
    if not year or not title:
        return None
    for film in films:
        for key in ("EngName", "HebName"):
            if not release.mentions(str(film.get(key) or ""), title):
                continue
            stated = re.search(r"(19|20)\d\d", str(film.get("ReleaseDate")
                                                   or film.get("Year") or ""))
            if stated and abs(int(stated.group(0)) - year) <= 1:
                return film
    return None


def _versions(meta, film_id, cookie):
    """The subtitle versions listed for one title.

    A film and an episode are fetched from different places. `MovieInfo.aspx`
    accepts `&season=&episode=` and *ignores them*, so a series asked this way
    is handed season one's page whatever was wanted - a wrong episode rather
    than none, which no filter downstream can catch. Episodes have their own
    fragment endpoint, which answers with bare `<tr>` rows and no table around
    them. The rows themselves are identical, so one parser reads both.
    """
    if meta.get("type") == "episode":
        url = EPISODE_LIST % (film_id, int(meta.get("season") or 1),
                              int(meta.get("episode") or 1))
    else:
        url = MOVIE_INFO % film_id

    response = http.get(url, headers=_signed_headers(cookie), timeout=TIMEOUT)
    if response is None or response.status_code >= 400:
        kodi.log("ktuvit did not return a subtitle list for %s" % film_id)
        return []

    try:
        html = response.text
    except Exception:
        return []

    found = []
    seen = set()
    for row in _ROWS.findall(html):
        if len(found) >= MAX_RESULTS:
            break
        identifier = _ROW_ID.search(row)
        name = _ROW_NAME.search(row)
        if not identifier or not name:
            continue                       # a heading, or the "no subtitles" row
        subtitle_id = identifier.group(1).strip()
        if not subtitle_id or subtitle_id in seen:
            continue                       # the same id is linked twice per row
        seen.add(subtitle_id)
        found.append(common.candidate(
            NAME, LANGUAGE, name.group(1).strip(),
            # The download needs two more calls, so the reference is carried
            # rather than a URL, and download() finishes the job.
            "%s|%s" % (film_id, subtitle_id),
        ))
    if not found:
        kodi.log("ktuvit listed no subtitles for %s" % film_id)
    return found


# --------------------------------------------------------------------------
# downloading
# --------------------------------------------------------------------------


def download(candidate):
    reference = candidate.get("download") or ""
    if "|" not in reference:
        return b""
    film_id, subtitle_id = reference.split("|", 1)

    cookie = session_cookie()
    if not cookie:
        return b""

    response, cookie = _post(
        REQUEST_DOWNLOAD,
        {"request": {"FilmID": film_id, "SubtitleID": subtitle_id,
                     "FontSize": 0, "FontColor": "", "PredefinedLayout": -1}},
        cookie)

    payload = _json_of(response)
    identifier = ""
    if isinstance(payload, dict):
        identifier = str(payload.get("DownloadIdentifier") or "")
    if not identifier:
        kodi.log("ktuvit would not issue a download id")
        return b""

    data = common.fetch_bytes(DOWNLOAD % identifier,
                              headers=_signed_headers(cookie))
    if _is_refusal(data):
        # A dead session fails *here* and nowhere earlier: requesting the
        # identifier succeeds anonymously, so `IsSuccess: true` on that call
        # says nothing about being signed in. The refusal arrives as HTTP 200
        # with a few dozen bytes of Hebrew prose, so only the body can tell it
        # from a subtitle. Worth one more try with a fresh cookie, because a
        # session cached for a day will sometimes have expired in it.
        cookie = session_cookie(refresh=True)
        if not cookie:
            return b""
        data = common.fetch_bytes(DOWNLOAD % identifier,
                                  headers=_signed_headers(cookie))
        if _is_refusal(data):
            kodi.log("ktuvit refused the download even after signing in again")
            return b""
    return common.extract_subtitle(data, LANGUAGE, candidate=candidate)


# "the download failed - please try again" and "the request was not found",
# which is what arrives instead of a file when the session is not signed in.
_REFUSALS = (u"ההורדה נכשלה",
             u"הבקשה לא נמצאה")


def _is_refusal(data):
    """Is this an error page wearing a subtitle's content type?

    A real subtitle is tens of kilobytes; these are under a few hundred bytes,
    so the size alone settles almost every case and the text settles the rest.
    """
    if not data:
        return True
    if len(data) > 1024:
        return False
    try:
        text = data.decode("windows-1255", "ignore")
    except Exception:
        return False
    return any(phrase in text for phrase in _REFUSALS)
