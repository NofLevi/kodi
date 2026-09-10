"""Ktuvit, the largest Hebrew subtitle source, which needs an account.

Every other provider here is anonymous. Ktuvit is not: it is a members' site,
and nothing at all comes back without a signed-in session. That is why it was
left out of the first release rather than shipped as a switch that did nothing.

The flow, which is four calls and one of them is only occasionally needed:

    MembershipService.svc/Login          email and a hashed password, in
                                         exchange for a session cookie
    ContentProvider.svc/SearchPage_search   find the film or series
    MovieInfo.aspx?ID=...                a page listing the subtitle versions
    RequestSubtitleDownload + DownloadFile.ashx
                                         ask for a download id, then use it

Two things are worth stating plainly.

The password is sent as a SHA-1 hex digest rather than as itself. That is what
the site's own client does; it is not encryption and it is not a security
feature this add-on is claiming, it is simply the wire format. The password is
still stored in Kodi's settings like every other credential here.

The session cookie is cached and reused. Logging in per search would be both
slow and rude to a small community site, so a session is kept for a day and
re-established only when a request comes back looking signed out.

This has been written against the documented flow and unit tested against
fixtures. It has never run against a real account, which is recorded in the
known gaps rather than glossed over.
"""
import hashlib
import re

from ... import cache, http, kodi, settings
from . import common

NAME = "ktuvit"
BASE = "https://members.ktuvit.me"
LANGUAGE = "he"

LOGIN = BASE + "/Services/MembershipService.svc/Login"
SEARCH = BASE + "/Services/ContentProvider.svc/SearchPage_search"
MOVIE_INFO = BASE + "/MovieInfo.aspx?ID=%s"
REQUEST_DOWNLOAD = BASE + "/Services/ContentProvider.svc/RequestSubtitleDownload"
DOWNLOAD = BASE + "/Services/DownloadFile.ashx?DownloadIdentifier=%s"

SESSION_TTL = 24 * 3600
TIMEOUT = (5, 12)

HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
    "Referer": BASE + "/",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
}

# The subtitle rows on MovieInfo.aspx. Each carries the internal id used to ask
# for a download and the release name the matcher scores against.
_ROW = re.compile(
    r'data-subtitle-id="([^"]+)".*?<div[^>]*class="[^"]*subtitle-name[^"]*"[^>]*>'
    r'\s*([^<]+?)\s*<', re.S | re.I)
# A simpler shape the site also uses, kept because one of the two always
# matches and neither is worth a dependency to parse properly.
_ROW_ALT = re.compile(
    r"downloadSubtitle\(['\"]([^'\"]+)['\"].*?>\s*([^<>]{3,120}?)\s*<", re.S)


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


def _hash_password(password):
    """Ktuvit's client sends a SHA-1 hex digest, so that is the wire format."""
    return hashlib.sha1(password.encode("utf-8")).hexdigest()


def session_cookie(refresh=False):
    """A signed-in cookie, cached so a search does not mean a login."""
    if not configured():
        return ""

    email, password = credentials()
    account = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]
    key = cache.make_key("subs", "ktuvit", "session", account)
    if not refresh:
        cached = cache.volatile_get(key)
        if cached:
            return cached

    response = http.post(
        LOGIN,
        json={"request": {"Email": email,
                          "Password": _hash_password(password)}},
        headers=HEADERS, timeout=TIMEOUT)

    if response is None or response.status_code >= 400:
        kodi.log("ktuvit login did not answer")
        return ""

    cookie = _cookie_from(response)
    if not cookie:
        kodi.log("ktuvit refused the sign in")
        return ""

    cache.volatile_set(key, cookie, SESSION_TTL)
    return cookie


def _cookie_from(response):
    """Pull the session cookie out of a login response."""
    jar = getattr(response, "cookies", None)
    if jar:
        try:
            for name in ("Login", ".AspNet.ApplicationCookie", "ASP.NET_SessionId"):
                value = jar.get(name)
                if value:
                    return "%s=%s" % (name, value)
        except Exception:
            pass

    header = (response.headers or {}).get("Set-Cookie", "")
    if header:
        return header.split(";")[0]
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
    for film in films:
        if not isinstance(film, dict):
            continue
        if wanted and imdb_id(film.get("ImdbID")) == wanted:
            return str(film.get("ID") or ""), cookie
    if wanted:
        kodi.log("ktuvit returned no title with the requested IMDb id")
        return "", cookie

    first = films[0] if isinstance(films[0], dict) else {}
    return str(first.get("ID") or ""), cookie


def _versions(meta, film_id, cookie):
    """The subtitle versions listed for one title."""
    url = MOVIE_INFO % film_id
    if meta.get("type") == "episode":
        url += "&season=%d&episode=%d" % (int(meta.get("season") or 1),
                                          int(meta.get("episode") or 1))

    response = http.get(url, headers=_signed_headers(cookie), timeout=TIMEOUT)
    if response is None or response.status_code >= 400:
        kodi.log("ktuvit did not return a subtitle list for %s" % film_id)
        return []

    try:
        html = response.text
    except Exception:
        return []

    rows = _ROW.findall(html) or _ROW_ALT.findall(html)
    if not rows:
        kodi.log("ktuvit listed no subtitles for %s" % film_id)
        return []

    found = []
    seen = set()
    for subtitle_id, release_name in rows:
        subtitle_id = subtitle_id.strip()
        if not subtitle_id or subtitle_id in seen:
            continue
        seen.add(subtitle_id)
        found.append(common.candidate(
            NAME, LANGUAGE, release_name.strip(),
            # The download needs two more calls, so the reference is carried
            # rather than a URL, and download() finishes the job.
            "%s|%s" % (film_id, subtitle_id),
        ))
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
    return common.extract_subtitle(data, LANGUAGE, candidate=candidate)
