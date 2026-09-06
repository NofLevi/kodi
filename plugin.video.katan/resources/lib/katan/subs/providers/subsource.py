"""SubSource, a Subscene replacement with a usable JSON API.

Its endpoints have moved more than once, so every response is read defensively:
a shape we do not recognise yields no candidates rather than an exception.
"""
from ... import http
from . import common

NAME = "subsource"
BASE = "https://api.subsource.net/api"

# SubSource names languages in full.
LANGUAGE_NAMES = {
    "he": ("hebrew",),
    "en": ("english",),
    "ar": ("arabic",),
}


def supports(language):
    return language in LANGUAGE_NAMES


def search(meta, target, languages):
    wanted = [code for code in languages if code in LANGUAGE_NAMES]
    if not wanted:
        return []

    movie = _find_title(meta)
    if not movie:
        return []

    payload = http.post_json("%s/getMovie" % BASE,
                             json={"movieName": movie,
                                   "langs": [LANGUAGE_NAMES[c][0] for c in wanted]},
                             timeout=(4, 9), default=None)
    subtitles = (payload or {}).get("subs")
    if not isinstance(subtitles, list):
        return []

    results = []
    for entry in subtitles:
        language = _language_code(entry.get("lang", ""))
        if language not in wanted:
            continue
        link = entry.get("subId") or entry.get("id")
        if not link:
            continue
        results.append(common.candidate(
            NAME, language,
            entry.get("releaseName") or entry.get("name") or "",
            link,
            downloads=int(entry.get("downloads") or 0),
            extra_movie=movie))
    return results


def _find_title(meta):
    query = meta.get("title") or ""
    if not query:
        return ""
    payload = http.post_json("%s/searchMovie" % BASE,
                             json={"query": query}, timeout=(4, 8), default=None)
    found = (payload or {}).get("found")
    if not isinstance(found, list) or not found:
        return ""
    year = int(meta.get("year") or 0)
    for entry in found:
        if year and str(year) in str(entry.get("releaseYear", "")):
            return entry.get("linkName") or entry.get("title", "")
    return found[0].get("linkName") or found[0].get("title", "")


def _language_code(name):
    lowered = (name or "").strip().lower()
    for code, names in LANGUAGE_NAMES.items():
        if lowered in names:
            return code
    return ""


def download(candidate):
    payload = http.post_json("%s/getSub" % BASE,
                             json={"movie": candidate.get("extra_movie", ""),
                                   "lang": candidate.get("language", ""),
                                   "id": candidate["download"]},
                             timeout=(5, 12), default=None)
    link = (payload or {}).get("sub", {}).get("downloadToken")
    if not link:
        return b""
    data = common.fetch_bytes("%s/downloadSub/%s" % (BASE, link))
    return common.extract_subtitle(data, candidate.get("language", ""))
