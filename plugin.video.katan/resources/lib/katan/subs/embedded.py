"""Subtitle tracks already inside the file being played.

These deserve to be first in the chooser and they deserve to say so. A track
muxed into the video was authored against that exact cut, so its timing is
correct by construction. Nothing downloaded can beat that, however well its
release name matches.

Kodi has already demuxed the file for playback, so listing these costs nothing:
no range requests, no container parsing, no download. Selecting one switches
the player's subtitle stream rather than writing a file.
"""
import json

import xbmc

from .. import kodi

PROVIDER = "embedded"

# Score used for an embedded track. It is the ceiling: a hash match is also
# 100, and both are exact, but an embedded track needs no download at all.
SCORE = 100

_LANGUAGE_ALIASES = {
    "he": ("he", "heb", "hebrew", "iw", u"\u05e2\u05d1\u05e8\u05d9\u05ea"),
    "en": ("en", "eng", "english"),
    "ar": ("ar", "ara", "arabic"),
    "ru": ("ru", "rus", "russian"),
    "es": ("es", "spa", "spanish"),
    "fr": ("fr", "fre", "fra", "french"),
}

# Tracks that are not full dialogue, and should not be offered as if they were.
_PARTIAL_MARKERS = ("forced", "signs", "songs", "commentary")


def streams():
    """Every subtitle track in the playing file: index, language and name.

    JSON-RPC carries the language and the track name; the Python Player API
    only gives a list of names. The richer source is tried first and the
    simpler one covers older builds.
    """
    found = _streams_via_jsonrpc()
    if found:
        return found
    return _streams_via_player()


def _streams_via_jsonrpc():
    request = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "Player.GetProperties",
        "params": {"playerid": 1, "properties": ["subtitles", "currentsubtitle"]},
    })
    try:
        payload = json.loads(xbmc.executeJSONRPC(request))
    except (ValueError, TypeError):
        return []

    result = (payload or {}).get("result") or {}
    out = []
    for entry in result.get("subtitles") or []:
        out.append({
            "index": int(entry.get("index", len(out))),
            "language": _code_for(entry.get("language") or entry.get("name") or ""),
            "name": entry.get("name") or entry.get("language") or "",
        })
    return out


def _streams_via_player():
    try:
        names = xbmc.Player().getAvailableSubtitleStreams() or []
    except Exception:
        return []
    return [{"index": index, "language": _code_for(name), "name": name}
            for index, name in enumerate(names)]


def _code_for(name):
    """Map whatever the container calls a language onto an ISO code."""
    lowered = (name or "").strip().lower()
    for code, aliases in _LANGUAGE_ALIASES.items():
        if lowered in aliases:
            return code
        for alias in aliases:
            if alias in lowered:
                return code
    return lowered[:2] if lowered else ""


def is_partial(name):
    """Forced or signs-only tracks translate captions, not the dialogue."""
    lowered = (name or "").lower()
    return any(marker in lowered for marker in _PARTIAL_MARKERS)


def candidates(languages=None):
    """Embedded tracks as chooser candidates, in the wanted language order."""
    wanted = list(languages or [])
    found = []
    for stream in streams():
        language = stream["language"]
        if wanted and language not in wanted:
            continue
        partial = is_partial(stream["name"])
        found.append({
            "provider": PROVIDER,
            "language": language,
            "release": stream["name"] or language,
            "download": str(stream["index"]),
            "stream_index": stream["index"],
            "score": SCORE - (30 if partial else 0),
            "reason": "embedded",
            "embedded": True,
            "partial": partial,
        })

    order = {code: position for position, code in enumerate(wanted)}
    found.sort(key=lambda c: (order.get(c["language"], len(order)), -c["score"]))
    return found


def select(index):
    """Switch the player to an embedded track. Returns True on success."""
    try:
        player = xbmc.Player()
        player.setSubtitleStream(int(index))
        player.showSubtitles(True)
    except Exception:
        kodi.log_exception("could not switch to embedded track %s" % index)
        return False
    kodi.log("selected embedded subtitle track %s" % index)
    return True


def has_language(language):
    return any(stream["language"] == language for stream in streams())
