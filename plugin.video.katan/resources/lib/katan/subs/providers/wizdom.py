"""Wizdom, the main open source of Hebrew subtitles.

Its API is simple and public: search by IMDb id, optionally with a season and
episode, and download the result as a zip. It also reports a score per version,
which the matcher folds in as a provider sync hint.
"""
from ... import http
from . import common

NAME = "wizdom"
BASE = "https://wizdom.xyz"
LANGUAGE = "he"


def supports(language):
    return language == LANGUAGE


def search(meta, target, languages):
    if LANGUAGE not in languages:
        return []
    imdb = (meta.get("ids") or {}).get("imdb")
    if not imdb:
        return []

    params = {"action": "by_id", "imdb": imdb}
    if meta.get("type") == "episode":
        params["season"] = int(meta.get("season") or 1)
        params["episode"] = int(meta.get("episode") or 1)

    payload = http.get_json("%s/api/search" % BASE, params=params,
                            timeout=(4, 8), default=None)
    if not isinstance(payload, list):
        return []

    results = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        subtitle_id = entry.get("id")
        if not subtitle_id:
            continue
        results.append(common.candidate(
            NAME, LANGUAGE,
            entry.get("versioname") or entry.get("version") or "",
            "%s/api/files/sub/%s" % (BASE, subtitle_id),
            downloads=int(entry.get("downloads") or 0),
            sync_percent=_sync_percent(entry),
        ))
    return results


def _sync_percent(entry):
    """Wizdom scores versions out of five; express it as a percentage."""
    try:
        return min(100, float(entry.get("score") or 0) * 20)
    except (TypeError, ValueError):
        return 0


def download(candidate):
    data = common.fetch_bytes(candidate["download"])
    return common.extract_subtitle(data, LANGUAGE, candidate=candidate)
