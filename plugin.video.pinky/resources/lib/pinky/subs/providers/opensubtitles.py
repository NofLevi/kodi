"""OpenSubtitles.com, the REST API v1.

The same corpus as `rest.opensubtitles.org`, behind a modern API that wants
an `Api-Key` on every request. A key of the viewer's own is used first. With
none - which is everybody - the keys DarkSubs publishes for its own add-on
are used, by permission of its author: the list is fetched from where
DarkSubs fetches it rather than copied here, so a key its author retires or
replaces is retired or replaced here too, and none is ever in this
repository.

What a key is for, measured on 6 October 2026: **search answers any key at
all**, a junk one included, and only a download needs a real one - a wrong
key is a 503, a spent one a 406. The download quota is one counter for the
caller rather than one per key: three downloads through three different keys
read 99, 97 and 95 remaining of 100. So walking the list buys a key that
works, not more downloads, and a hundred a day is a long way from one
subtitle per film.
"""
import random
import time
from urllib.parse import urlsplit

from ... import cache, http, kodi, settings
from . import common

NAME = "opensubtitles"
BASE = "https://api.opensubtitles.com/api/v1"
USER_AGENT = "Pinky v0.1.0"
KEYS_URL = ("https://morantheking.github.io/Kodi-POV-IL/repository/other/"
            "DarkSubs_OpenSubtitles/darksubs_opensubtitles_api.json")
KEYS_TTL = 24 * 3600
# Statuses that are about the key rather than the question, so the next key
# may get a different answer.
REFUSED = (401, 403, 406, 503)
SEARCH_KEYS = 3
DOWNLOAD_KEYS = 4
# The API answers fifty rows a page. One request for every language is cheap
# when it holds them all; when it does not, a busy language would push a
# rare one off the page, so each is asked alone.
PAGE = 50
# Asked as the regional variants the API files them under.
_ASKED = {"pt": "pt-br,pt-pt", "zh": "zh-cn,zh-tw"}

_token = [""]
_shuffled = []


def api_key():
    return settings.get("subs.opensubtitles.apikey").strip()


def _shared_keys():
    """The published key list, refreshed daily and kept a month.

    A fetch that fails keeps the list it already had, and a box that has
    never had one asks again in ten minutes rather than on every search.
    """
    held = cache.get("opensubtitles.keys") or {}
    if held and time.time() - held.get("at", 0) < KEYS_TTL:
        return held.get("keys") or []
    payload = http.get_json(KEYS_URL, timeout=(4, 8), default=None)
    found = []
    for entry in payload if isinstance(payload, list) else []:
        value = entry.get("OS_API_KEY_VALUE") if isinstance(entry, dict) else None
        if isinstance(value, str) and value.strip() and value.strip() not in found:
            found.append(value.strip())
    if found:
        cache.set("opensubtitles.keys", {"at": time.time(), "keys": found}, 30 * 86400)
        return found
    keys = held.get("keys") or []
    cache.set("opensubtitles.keys",
              {"at": time.time() - KEYS_TTL + 600, "keys": keys}, 30 * 86400)
    return keys


def _keys():
    """The viewer's own key, then the shared ones in an order of this
    process's own, so a household's searches do not all lean on one key.
    A generator: the list is fetched only when the own key is missing or
    refused."""
    own = api_key()
    if own:
        yield own
    if not _shuffled:
        keys = list(_shared_keys())
        random.shuffle(keys)
        _shuffled.extend(keys)
    for key in _shuffled:
        if key != own:
            yield key


def configured():
    return bool(api_key()) or bool(_shared_keys())


def supports(language):
    return True


def _headers(key, with_token=False):
    headers = {
        "Api-Key": key,
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if with_token and _token[0]:
        headers["Authorization"] = "Bearer %s" % _token[0]
    return headers


def _ask(method, path, tries, with_token=False, **kwargs):
    """The JSON answer of the first key that is not refused, or None.

    A connection that fails, a 429 or a refusal of the question itself ends
    it, because another key would be asked the same thing by the same box.
    """
    for number, key in enumerate(_keys()):
        if number >= tries:
            break
        response = http.request(method, BASE + path, retries=0,
                                headers=_headers(key, with_token), **kwargs)
        if response is None:
            return None
        if response.status_code in REFUSED:
            kodi.log("OpenSubtitles.com refused a key (%s), trying the next"
                     % response.status_code)
            continue
        return http._json_or(response, None)
    return None


def login():
    """Signing in raises the daily download quota. It is optional."""
    user = settings.get("subs.opensubtitles.user").strip()
    password = settings.get("subs.opensubtitles.password").strip()
    if not (user and password):
        return False
    payload = _ask("POST", "/login", SEARCH_KEYS,
                   json={"username": user, "password": password},
                   timeout=(5, 10))
    _token[0] = (payload or {}).get("token", "") if isinstance(payload, dict) else ""
    return bool(_token[0])


def _positive_integer(value):
    """Return an exact positive signed 64-bit integer without coercion."""
    maximum = (1 << 63) - 1
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if 0 < value <= maximum else 0
    if (isinstance(value, str) and value.isascii() and value.isdigit()
            and len(value) <= 19):
        number = int(value)
        return number if 0 < number <= maximum else 0
    return 0


def _address(meta, numbering, video_hash, file_size):
    """The search parameters for one numbering of this title."""
    ids = meta.get("ids") or {}
    imdb = str(ids.get("imdb") or "").replace("tt", "").strip()
    params = {}
    if video_hash:
        params["moviehash"] = video_hash
        exact_size = _positive_integer(file_size)
        if exact_size:
            # Current first-party clients and the live REST endpoint support
            # this constraint even though the published Stoplight schema omits
            # it.
            params["moviebytesize"] = exact_size
    if numbering:
        # The show's id: an episode's own is a different number, and the
        # show is what we carry.
        if imdb:
            params["parent_imdb_id"] = imdb
        elif ids.get("tmdb"):
            params["parent_tmdb_id"] = ids["tmdb"]
        else:
            params["query"] = meta.get("show_title") or meta.get("title", "")
        params["season_number"], params["episode_number"] = numbering
        params["type"] = "episode"
    else:
        if imdb:
            params["imdb_id"] = imdb
        elif ids.get("tmdb"):
            params["tmdb_id"] = ids["tmdb"]
        else:
            params["query"] = meta.get("title", "")
        params["type"] = "movie"
    return params


def _page(params, languages):
    """(rows, whole) for one request: `whole` is False when the answer had
    more rows than one page could hold."""
    asked = sorted(set(",".join(_ASKED.get(language, language)
                                for language in languages).split(",")))
    # Sorted, because the API redirects any other order to this one.
    query = sorted(dict(params, languages=",".join(asked)).items())
    payload = _ask("GET", "/subtitles", SEARCH_KEYS, params=query, timeout=(4, 9))
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return [], True
    rows = payload["data"]
    total = payload.get("total_count")
    whole = not (isinstance(total, int) and total > len(rows) and len(rows) >= PAGE)
    return rows, whole


def search(meta, target, languages, video_hash="", file_size=0):
    languages = [language for language in languages if language]
    if not languages:
        return []
    numberings = common.episode_numberings(meta) or [None]
    rows = []
    for numbering in numberings:
        params = _address(meta, numbering, video_hash, file_size)
        rows, whole = _page(params, languages)
        if not whole and len(languages) > 1:
            rows = []
            for language in languages:
                rows.extend(_page(params, [language])[0])
        if rows:
            # The first numbering that answers is the one the uploads use.
            break
    return _candidates(rows)


def _candidates(data):
    results = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        attributes = entry.get("attributes")
        if not isinstance(attributes, dict):
            continue
        files = attributes.get("files")
        if not isinstance(files, list) or len(files) != 1:
            continue
        if "nb_cd" in attributes:
            parts = _positive_integer(attributes.get("nb_cd"))
            if parts != 1:
                continue
        file_entry = files[0]
        if not isinstance(file_entry, dict):
            continue
        file_id = _positive_integer(file_entry.get("file_id"))
        if not file_id:
            continue
        # One candidate must represent one complete subtitle. Returning CD1 of
        # a multi-file upload silently stops halfway through the film.
        language = attributes.get("language")
        if not isinstance(language, str) or not language.strip():
            continue
        # "pt-BR" is Portuguese to everything downstream.
        language = language.strip().lower().split("-")[0]
        release_name = attributes.get("release")
        if not isinstance(release_name, str):
            release_name = ""
        results.append(common.candidate(
            NAME,
            language,
            release_name,
            file_id,
            downloads=_positive_integer(attributes.get("download_count")),
            # This response does not carry the exact media byte size. Without
            # that independent equality check, a server hash claim is useful
            # metadata but not exact-file proof and must never become a timing
            # oracle that can reject another subtitle.
            hash_match=False,
            hearing_impaired=bool(attributes.get("hearing_impaired")),
            fps=attributes.get("fps") or 0,
        ))
    return results


def download(candidate):
    """Exchange a file id for a link, then fetch it.

    A failure here is usually the daily quota, which is worth saying plainly
    rather than reporting as a generic subtitle error.
    """
    payload = _ask("POST", "/download", DOWNLOAD_KEYS, with_token=True,
                   json={"file_id": candidate["download"]}, timeout=(5, 12))
    if not isinstance(payload, dict):
        return b""
    remaining = payload.get("remaining")
    if not isinstance(remaining, bool) and remaining in (0, "0"):
        kodi.log("OpenSubtitles daily quota is exhausted")
    link = payload.get("link")
    if not isinstance(link, str):
        return b""
    link = link.strip()
    try:
        parsed_link = urlsplit(link)
        if parsed_link.scheme.lower() not in ("http", "https") \
                or not parsed_link.hostname:
            return b""
    except ValueError:
        return b""
    return common.extract_subtitle(common.fetch_bytes(link),
                                   candidate.get("language", ""),
                                   candidate=candidate)
