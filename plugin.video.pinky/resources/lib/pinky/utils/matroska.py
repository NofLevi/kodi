# -*- coding: utf-8 -*-
"""The subtitle tracks an MKV declares, read from the first bytes of the file.

A fansub release carries its English subtitle *inside* the MKV - SubsPlease,
Judas, Sokudo, Erai-raws all mux it rather than shipping a file beside the
video - and a track inside the file is in time by construction. The picker
had no way to know, so it quoted a stranger's upload at 70% beside a file
that already had subtitles in it.

Matroska declares its tracks near the start: EBML header, then the Segment,
whose SeekHead, Info and Tracks come before the first Cluster in everything
mkvmerge writes. So the first few hundred kilobytes answer the question
without downloading an episode. This only walks that structure; fetching the
bytes is the caller's business.

Returns None when the answer is not in the bytes given - not an MKV, or the
Tracks element lies further in - so "unknown" is never mistaken for "none".
"""

EBML = 0x1A45DFA3
SEGMENT = 0x18538067
TRACKS = 0x1654AE6B
TRACK_ENTRY = 0xAE
TRACK_TYPE = 0x83
CODEC_ID = 0x86
LANGUAGE = 0x22B59C
LANGUAGE_BCP47 = 0x22B59D
NAME = 0x536E
FLAG_FORCED = 0x55AA
CLUSTER = 0x1F43B675

SUBTITLE = 0x11

# ISO 639-2 as Matroska writes it, to the two letters the rest of the add-on
# uses. Only what anybody here reads or translates from.
_TWO_LETTER = {"eng": "en", "heb": "he", "ara": "ar", "spa": "es", "fre": "fr",
               "fra": "fr", "rus": "ru", "pol": "pl", "por": "pt", "ita": "it",
               "ger": "de", "deu": "de", "jpn": "ja", "kor": "ko", "chi": "zh",
               "zho": "zh", "tur": "tr", "und": "und"}

# A "Signs & Songs" track captions the on-screen text and the lyrics, not the
# dialogue: counting it as the episode's subtitle would promise a viewer
# something the file does not have.
_PARTIAL_WORDS = ("sign", "song", "forced", "karaoke", "commentary")


def _vint(data, pos, keep_marker):
    if pos >= len(data):
        return None, pos
    first = data[pos]
    length = 1
    mask = 0x80
    while length <= 8 and not first & mask:
        length += 1
        mask >>= 1
    if length > 8 or pos + length > len(data):
        return None, pos
    value = first if keep_marker else first & (mask - 1)
    for byte in data[pos + 1:pos + length]:
        value = (value << 8) | byte
    return value, pos + length


def _element(data, pos):
    """(id, size, start of payload, end of payload) - size None if unknown."""
    ident, after_id = _vint(data, pos, True)
    if ident is None:
        return None
    size, start = _vint(data, after_id, False)
    if size is None:
        return None
    width = start - after_id
    unknown = size == (1 << (7 * width)) - 1
    end = len(data) if unknown else start + size
    return ident, (None if unknown else size), start, end


def _children(data, start, end):
    pos = start
    while pos < end:
        found = _element(data, pos)
        if found is None:
            return
        ident, size, payload, stop = found
        yield ident, data[payload:min(stop, len(data))], stop > len(data)
        if size is None:
            return
        pos = stop


def _number(raw):
    value = 0
    for byte in raw:
        value = (value << 8) | byte
    return value


def _text(raw):
    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()


def _track(payload):
    entry = {"type": 0, "codec": "", "language": "eng", "name": "",
             "forced": False}
    for ident, raw, _truncated in _children(payload, 0, len(payload)):
        if ident == TRACK_TYPE:
            entry["type"] = _number(raw)
        elif ident == CODEC_ID:
            entry["codec"] = _text(raw)
        elif ident == LANGUAGE:
            entry["language"] = _text(raw) or "eng"
        elif ident == LANGUAGE_BCP47:
            entry["bcp47"] = _text(raw)
        elif ident == NAME:
            entry["name"] = _text(raw)
        elif ident == FLAG_FORCED:
            entry["forced"] = bool(_number(raw))
    return entry


def subtitle_tracks(data):
    """Every subtitle track declared in these leading bytes, or None.

    Each is {"language", "codec", "name", "partial"}: two letters where the
    language is one this add-on knows, "und" where the file says it does
    not know. Matroska's default language is English, so a track that
    names none is English by the specification, not by a guess.
    """
    data = bytes(data or b"")
    top = _element(data, 0)
    if top is None or top[0] != EBML:
        return None
    pos = top[3]
    segment = _element(data, pos)
    if segment is None or segment[0] != SEGMENT:
        return None
    for ident, payload, truncated in _children(data, segment[2], segment[3]):
        if ident == CLUSTER:
            return None
        if ident != TRACKS:
            continue
        if truncated:
            return None
        found = []
        for child, raw, cut in _children(payload, 0, len(payload)):
            if child != TRACK_ENTRY or cut:
                continue
            track = _track(raw)
            if track["type"] != SUBTITLE:
                continue
            code = (track.get("bcp47") or "").split("-", 1)[0].lower()
            if len(code) != 2:
                code = _TWO_LETTER.get(track["language"].lower(),
                                       track["language"].lower())
            name = track["name"].lower()
            found.append({"language": code, "codec": track["codec"],
                          "name": track["name"],
                          "partial": track["forced"] or any(
                              word in name for word in _PARTIAL_WORDS)})
        return found
    return None
