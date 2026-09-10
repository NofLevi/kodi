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
    # Japanese and Korean were missing, which mattered the moment anything
    # looked at *audio* tracks rather than subtitles: `_code_for` falls back to
    # the first two letters, so a track called "Japanese" was reported as "ja"
    # by luck and one called "jpn" as "jp", which is not a language code.
    "ja": ("ja", "jpn", "jap", "japanese"),
    "ko": ("ko", "kor", "korean"),
    "it": ("it", "ita", "italian"),
    "de": ("de", "ger", "deu", "german"),
    "pt": ("pt", "por", "portuguese"),
    "tr": ("tr", "tur", "turkish"),
}

# For saying "Japanese" rather than "ja" on a television.
LANGUAGE_NAMES = {
    "he": "Hebrew", "en": "English", "ar": "Arabic", "ru": "Russian",
    "es": "Spanish", "fr": "French", "ja": "Japanese", "ko": "Korean",
    "it": "Italian", "de": "German", "pt": "Portuguese", "tr": "Turkish",
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
        "params": {"playerid": 1,
                   "properties": ["subtitles", "currentsubtitle",
                                  "audiostreams", "currentaudiostream"]},
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
    """Map whatever the container calls a language onto an ISO code.

    Exact matches first, and only then substrings - and substrings only for
    aliases of three letters or more. A two-letter alias inside a longer word
    is a coincidence, not a language: "es" is in "japan**es**e", so a track
    named Japanese was reported as Spanish. That was wrong for subtitles long
    before anything looked at audio; it only became visible when a test asked
    about the two languages this was built for.
    """
    lowered = (name or "").strip().lower()
    if not lowered:
        return ""
    for code, aliases in _LANGUAGE_ALIASES.items():
        if lowered in aliases:
            return code
    for code, aliases in _LANGUAGE_ALIASES.items():
        for alias in aliases:
            if len(alias) >= 3 and alias in lowered:
                return code
    return lowered[:2]


def audio_streams():
    """Every audio track in the playing file: index, language and name.

    The same JSON-RPC call the subtitle list uses already carries these; it
    simply never asked for them. Nothing in this add-on has ever looked at
    audio, so which track a viewer hears is Kodi's default applied to whatever
    file was picked - and for a dual-audio anime release that is Japanese or
    English by luck.

    This does not choose one. It is here so the viewer can be told there is
    something to choose, which is the part Kodi's own switcher cannot do.
    """
    request = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "Player.GetProperties",
        "params": {"playerid": 1,
                   "properties": ["audiostreams", "currentaudiostream"]},
    })
    try:
        payload = json.loads(xbmc.executeJSONRPC(request))
    except (ValueError, TypeError):
        return []

    result = (payload or {}).get("result") or {}
    out = []
    for entry in result.get("audiostreams") or []:
        name = entry.get("name") or entry.get("language") or ""
        out.append({
            "index": int(entry.get("index", len(out))),
            "language": _code_for(entry.get("language") or name),
            "name": name,
        })
    return out


def audio_languages():
    """The distinct languages on offer, named for a human, in track order.

    Duplicates are dropped: a file with a stereo and a 5.1 English track has
    two audio streams and one choice, and offering "English, English" would be
    a worse answer than saying nothing.
    """
    names = []
    for stream in audio_streams():
        code = stream.get("language") or ""
        label = LANGUAGE_NAMES.get(code) or (stream.get("name") or code or "?")
        if label not in names:
            names.append(label)
    return names


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


def select(index, player=None):
    """Switch the supplied/current player to an embedded track."""
    try:
        player = player or xbmc.Player()
        player.setSubtitleStream(int(index))
        player.showSubtitles(True)
    except Exception:
        kodi.log_exception("could not switch to embedded track %s" % index)
        return False
    kodi.log("selected embedded subtitle track %s" % index)
    return True
