"""OpenSubtitles REST API v1.

This is the only provider that can match on the file hash, which is why the
pipeline asks it first when a hash is available. The cost is a strict download
quota, so a hash match is used before anything else and the quota is never
spent on speculative downloads.
"""
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


def search(meta, target, languages, video_hash="", file_size=0):
    if not configured():
        return []

    params = {"languages": ",".join(languages)}
    ids = meta.get("ids") or {}
    if video_hash:
        params["moviehash"] = video_hash
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
    for entry in payload.get("data") or []:
        attributes = entry.get("attributes") or {}
        files = attributes.get("files") or []
        if not files:
            continue
        results.append(common.candidate(
            NAME,
            attributes.get("language", "") or "",
            attributes.get("release") or "",
            files[0].get("file_id"),
            downloads=int(attributes.get("download_count") or 0),
            hash_match=bool(attributes.get("moviehash_match")),
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
    if not payload:
        return b""
    if payload.get("remaining") is not None and int(payload["remaining"]) <= 0:
        kodi.log("OpenSubtitles daily quota is exhausted")
    link = payload.get("link")
    if not link:
        return b""
    return common.extract_subtitle(common.fetch_bytes(link))
