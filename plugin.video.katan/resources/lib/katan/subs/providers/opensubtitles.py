"""OpenSubtitles REST API v1.

This is the only provider that can match on the file hash, which is why the
pipeline asks it first when a hash is available. The cost is a strict download
quota, so a hash match is used before anything else and the quota is never
spent on speculative downloads.
"""
from urllib.parse import urlsplit

from ... import http, kodi, settings
from . import common

NAME = "opensubtitles"
BASE = "https://api.opensubtitles.com/api/v1"
USER_AGENT = "Katan v0.1.0"

_token = [""]


def api_key():
    return settings.get("subs.opensubtitles.apikey").strip()


def configured():
    return bool(api_key())


def supports(language):
    return True


def _headers(with_token=False):
    headers = {
        "Api-Key": api_key(),
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if with_token and _token[0]:
        headers["Authorization"] = "Bearer %s" % _token[0]
    return headers


def login():
    """Signing in raises the daily download quota. It is optional."""
    user = settings.get("subs.opensubtitles.user").strip()
    password = settings.get("subs.opensubtitles.password").strip()
    if not (user and password and configured()):
        return False
    payload = http.post_json("%s/login" % BASE, headers=_headers(),
                             json={"username": user, "password": password},
                             timeout=(5, 10), default=None)
    _token[0] = (payload or {}).get("token", "")
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


def search(meta, target, languages, video_hash="", file_size=0):
    if not configured():
        return []

    params = {"languages": ",".join(languages)}
    ids = meta.get("ids") or {}
    if video_hash:
        params["moviehash"] = video_hash
        exact_size = _positive_integer(file_size)
        if exact_size:
            # Current first-party clients and the live REST endpoint support
            # this constraint even though the published Stoplight schema omits
            # it.
            params["moviebytesize"] = exact_size
    if ids.get("imdb"):
        params["imdb_id"] = str(ids["imdb"]).replace("tt", "")
    elif ids.get("tmdb"):
        params["tmdb_id"] = ids["tmdb"]
    else:
        params["query"] = meta.get("title", "")

    if meta.get("type") == "episode":
        params["season_number"] = int(meta.get("season") or 1)
        params["episode_number"] = int(meta.get("episode") or 1)
        params["type"] = "episode"
    else:
        params["type"] = "movie"

    payload = http.get_json("%s/subtitles" % BASE, params=params,
                            headers=_headers(), timeout=(4, 9), default=None)
    if not payload:
        return []

    results = []
    data = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(data, list):
        return []
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
        language = language.strip().lower()
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
    if not configured():
        return b""
    payload = http.post_json("%s/download" % BASE, headers=_headers(True),
                             json={"file_id": candidate["download"]},
                             timeout=(5, 12), default=None)
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
