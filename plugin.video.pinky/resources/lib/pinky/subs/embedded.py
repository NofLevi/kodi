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
    "zh": ("zh", "chi", "zho", "chinese", "mandarin", "cantonese"),
}

# For saying "Japanese" rather than "ja" on a television.
LANGUAGE_NAMES = {
    "he": "Hebrew", "en": "English", "ar": "Arabic", "ru": "Russian",
    "es": "Spanish", "fr": "French", "ja": "Japanese", "ko": "Korean",
    "it": "Italian", "de": "German", "pt": "Portuguese", "tr": "Turkish",
    "zh": "Chinese",
}

# Tracks that are not full dialogue, and should not be offered as if they
# were: they caption on-screen text rather than translating it. Named in
# more than English because the releases are: a German or Spanish anime rip
# labels its forced track in its own language, and calling that one "the
# English subtitles" is how a file ends up playing with nothing readable on
# it. "s&s" and "signs & songs" are what fansub groups actually write.
_PARTIAL_MARKERS = (
    "forced", "signs", "songs", "commentary", "s&s", "sign/song",
    "erzwungen", "forzado", "forzati", "forcé", "forcee",
    "принуд", "karaoke", "typeset",
)


# How long to keep asking for a track list that has names on it. Kodi
# enumerates the tracks while it opens the file, so the first question - asked
# the moment onAVStarted fires - can come back empty or nameless.
_NAME_ATTEMPTS = 4
_NAME_WAIT_MS = 400


def streams():
    """Every subtitle track in the playing file: index, language and name.

    JSON-RPC carries the language *and the track name*; the Python Player API
    gives only a list of languages. That difference is the whole of this
    function, because without names nothing can tell a signs-and-songs track
    from the dialogue.

    Black Lagoon 1x04 is what taught it. The file has four English tracks:

        English Lyrics/Signs [default]   English Subtitles
        English BD (Signs / Songs)       English BD (Full)

    The first is signs-only and the file marks it default, which is what a
    fansub does for people watching the dub. Asked without names all four
    read as plain "English", none looked partial, index 0 won, and the
    episode played with a track selected and nothing on screen.

    So a nameless answer is treated as "not ready yet" and asked again before
    it is believed. The Python fallback is the last resort rather than the
    second option, and it says what it costs.
    """
    found = []
    for attempt in range(_NAME_ATTEMPTS):
        found = _streams_via_jsonrpc()
        if found and any(track["name"] for track in found):
            return found
        if attempt + 1 < _NAME_ATTEMPTS:
            xbmc.sleep(_NAME_WAIT_MS)
    if found:
        kodi.log("the player named none of its %d subtitle tracks, so a "
                 "signs-only track cannot be told from the dialogue"
                 % len(found), kodi.LOG_INFO)
        return found
    fallback = _streams_via_player()
    if fallback:
        kodi.log("subtitle tracks came from the Python player API, which "
                 "carries no track names", kodi.LOG_INFO)
    return fallback


def _active_player():
    """The video player's id, which is not always 1.

    It was hardcoded, and a wrong id answers with an error rather than a
    track list - which this then read as "no tracks" and fell through to the
    nameless fallback.
    """
    try:
        payload = json.loads(xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "Player.GetActivePlayers"})))
    except (ValueError, TypeError):
        return 1
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, list):
        for player in result:
            if isinstance(player, dict) and player.get("type") == "video":
                try:
                    return int(player.get("playerid"))
                except (TypeError, ValueError):
                    break
    return 1


def _streams_via_jsonrpc():
    request = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "Player.GetProperties",
        "params": {"playerid": _active_player(),
                   "properties": ["subtitles", "currentsubtitle",
                                  "audiostreams", "currentaudiostream"]},
    })
    try:
        payload = json.loads(xbmc.executeJSONRPC(request))
    except (ValueError, TypeError):
        return []

    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return []                          # no player, or an answer of another shape
    return _tracks(result.get("subtitles"))


def _tracks(entries):
    """Read a track list, skipping anything that is not one.

    Every field here comes from whatever demuxed the file, so none of it is
    ours to trust. A track whose index is missing, `None` or a word used to
    raise out of this function - and this runs inside `coordinator.commit`,
    which holds a process-wide lock, so one odd container took the lock with
    it. A track that cannot be read is simply not a track.
    """
    if not isinstance(entries, (list, tuple)):
        return []
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("index", len(out)))
        except (TypeError, ValueError):
            continue
        out.append({
            "index": index,
            "language": _code_for(entry.get("language") or entry.get("name") or ""),
            "name": entry.get("name") or entry.get("language") or "",
            # Kodi 20 and later carry these on every stream. They are the only
            # thing that separates two tracks a container calls "English" and
            # "English".
            "forced": bool(entry.get("isforced")),
            "impaired": bool(entry.get("isimpaired")),
            "default": bool(entry.get("isdefault")),
        })
    return out


def _streams_via_player():
    try:
        names = xbmc.Player().getAvailableSubtitleStreams() or []
    except Exception:
        return []
    return [{"index": index, "language": _code_for(name), "name": name,
             "forced": False, "impaired": False, "default": False}
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

    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return []
    playing = result.get("currentaudiostream")
    current = playing.get("index") if isinstance(playing, dict) else None
    out = []
    for track in _tracks(result.get("audiostreams")):
        track["current"] = current is not None and track["index"] == current
        out.append(track)
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
        # Forced and hearing-impaired are the container saying it itself; the
        # name is how a fansub says it. Either one means this track is not
        # the dialogue.
        partial = (is_partial(stream["name"]) or stream.get("forced")
                   or stream.get("impaired"))
        found.append({
            "provider": PROVIDER,
            "language": language,
            "release": stream["name"] or language,
            "download": str(stream["index"]),
            "stream_index": stream["index"],
            "score": SCORE - (30 if partial else 0),
            "reason": "embedded",
            "embedded": True,
            "partial": bool(partial),
            "default": bool(stream.get("default")),
        })

    order = {code: position for position, code in enumerate(wanted)}
    # Language first, then the dialogue tracks, then **not** the default one.
    #
    # That last term looks backwards and is the whole point. Measured on two
    # different Black Lagoon releases: one names its four tracks and marks
    # "English Lyrics/Signs" default; the other names both of its tracks
    # "English" and marks the signs-only one default. A fansub marks the signs
    # track default on purpose, because it is what somebody watching the dub
    # should get automatically - so on a file with several tracks in one
    # language, default is evidence *against* a track being the dialogue.
    #
    # Only as a tie-break, and only when a language has more than one track.
    # A file with a single default English track is not affected by this at
    # all, which is nearly every file that is not anime.
    multiple = set()
    seen = set()
    for candidate in found:
        if candidate["language"] in seen:
            multiple.add(candidate["language"])
        seen.add(candidate["language"])
    found.sort(key=lambda c: (order.get(c["language"], len(order)),
                              -c["score"],
                              1 if (c["default"] and c["language"] in multiple)
                              else 0,
                              c["stream_index"]))
    return found


def select(index, player=None, name=""):
    """Switch the supplied/current player to an embedded track.

    Reports what actually happened. The playback facade in `player.py`
    discards writes once playback has moved on, and this used to log
    "selected embedded subtitle track 0" either way - so a bare screen and a
    switched track left exactly the same line behind. The track's own name
    goes in the line too, because "track 0" alone cannot be told apart from
    a signs-and-songs track that is doing its job perfectly.
    """
    try:
        player = player or xbmc.Player()
        switched = player.setSubtitleStream(int(index))
        shown = player.showSubtitles(True)
    except Exception:
        kodi.log_exception("could not switch to embedded track %s" % index)
        return False
    if switched is False or shown is False:
        kodi.log("playback moved on before embedded track %s could be "
                 "selected, so nothing was switched" % index, kodi.LOG_INFO)
        return False
    kodi.log("selected embedded subtitle track %s (%s)"
             % (index, name or "unnamed"))
    return True
