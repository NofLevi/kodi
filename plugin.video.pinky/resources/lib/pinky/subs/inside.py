"""The subtitle track inside the file being played, as a clock.

Whether a downloaded subtitle is in time was a judgement about its *name*:
"same source and resolution" is 70%, and measured against an independent
upload that guess was out by 14 seconds on Obsession and 32 on Howl's Moving
Castle. The only real ruler was a subtitle matched by file hash, which exists
for about one title in ten.

The file usually carries a better one. A release with a subtitle track muxed
in has every line of it in the Matroska index - start, duration, position -
and the index is small: 98 KB for Fight Club's 1,810 lines, 706 KB for a Silo
episode's 43 tracks, read in well under a second. Those times are when
somebody speaks in *this* cut, in whatever language the track is, which is
all `sync` needs - it correlates when people talk, not what they say.

Kodi POV IL's MoranSubs does the same with its `mkv_probe`; this is the idea,
not its code.
"""
import re
import threading
import time

from .. import http, kodi
from ..utils import matroska
from . import hasher, srt

HEAD_BYTES = 512 * 1024
# An index larger than this is not read: it is a ruler, not a download.
INDEX_LIMIT = 12 * 1024 * 1024
# Fewer lines than this is a signs track or a forced one, whatever it is
# called, and correlating dialogue against it would refuse good subtitles.
MIN_LINES = 40

_HELD = {}
_LOCK = threading.Lock()


def tracks(url):
    """Every subtitle track the file indexes: the `matroska.layout` entry
    plus "lines", its [(start, end, cluster position, position in it)].

    [] when the file is not Matroska, has no subtitle track, or indexes only
    its video - an older fansub mux, which would have to be read through.
    """
    if not url:
        return []
    with _LOCK:
        if url in _HELD:
            return _HELD[url]
    found = _read(url)
    with _LOCK:
        if len(_HELD) > 8:
            _HELD.clear()
        _HELD[url] = found
    return found


def _read(url):
    try:
        head = hasher.read_range(url, 0, HEAD_BYTES - 1)
        where = matroska.layout(head) if head else None
        if not where or not where["tracks"] or where["cues"] is None:
            return []
        start = where["cues"]
        opening = hasher.read_range(url, start, start + 15)
        element = matroska._element(bytes(opening or b""), 0)
        if element is None or element[0] != matroska.CUES or not element[1]:
            return []
        if element[1] > INDEX_LIMIT:
            kodi.log("the file's index is %d MB, too large to read as a ruler"
                     % (element[1] // (1024 * 1024)))
            return []
        body = hasher.read_range(url, start + element[2],
                                 start + element[2] + element[1] - 1)
        index = matroska.cue_index(body, where["scale"]) if body else {}
    except Exception:
        kodi.log_exception("could not read the subtitle index inside the file")
        return []
    found = []
    for track in where["tracks"]:
        lines = index.get(track["number"]) or []
        if lines:
            found.append(dict(track, lines=lines, segment=where["segment"]))
    return found


def timeline(url, language=None):
    """When the dialogue is on screen, from the fullest track inside the file.

    A list of `srt.Cue` with no text, or []. `language` asks for that
    language's track alone; without it any language serves, because the
    question is when somebody speaks and not what they say. A hearing-
    impaired track is the last choice: it also times the sound effects.
    """
    best = None
    for track in tracks(url):
        if track["partial"] or len(track["lines"]) < MIN_LINES:
            continue
        if language and track["language"] != language:
            continue
        name = track["name"].lower()
        described = "sdh" in name or name.endswith("cc") or "hearing" in name
        rank = (not described, track["language"] == "en", len(track["lines"]))
        if best is None or rank > best[0]:
            best = (rank, track)
    if best is None:
        return []
    track = best[1]
    kodi.log("the file's own %s subtitle track times %d lines, used as the ruler"
             % (track["language"], len(track["lines"])))
    return [srt.Cue(number, start, end, "")
            for number, (start, end, _cluster, _relative)
            in enumerate(sorted(track["lines"]), 1)]


# --------------------------------------------------------------------------
# the lines themselves, to translate from
# --------------------------------------------------------------------------

# One line of dialogue is a few hundred bytes; this much is read at each
# position the index names, and a block that turns out larger is read again.
WINDOW = 4 * 1024
LARGE_WINDOW = 64 * 1024
# Neighbouring lines closer than this are one request. In a small anime
# episode that merges most of them; in a film the lines are megabytes apart
# and each costs its own small read - never the video between them.
MERGE_GAP = 192 * 1024
MERGE_MAX = 2 * 1024 * 1024
TEXT_DEADLINE = 45.0
# Each line is a request, and the debrid CDN stops answering a client that
# sends too many: measured, 3,167 in 38 seconds for one film's track and
# every read after it failed for minutes. So a track is read whole only when
# it is an episode's worth of lines, three requests at a time - Naruto's 334
# took 23 seconds, Demon Slayer's 298 seven - and a feature film's 1,800 is
# left to the subtitles that can be downloaded, which a film nearly always
# has.
# ponytail: all lines before the first is shown. Reading ahead of the
# playhead instead would lift the cap, and is the upgrade if films need it.
MAX_LINES = 700
WORKERS = 4
# The limit is the account's, not this reader's: tripping it answers 429 to
# the player as well, so the film being watched stalls. Requests therefore
# start no closer together than this - a dozen a second, where fourteen to
# thirty were measured passing and eighty-three failing - and the first
# refusal ends the read rather than being argued with.
PACE = 0.05
# A track whose lines mostly cannot be read is not handed to a translator.
MIN_READ = 0.8

_ASS_TAGS = re.compile(r"\{[^}]*\}")
_MARKUP = re.compile(r"</?[a-zA-Z][^>]*>")
_SIMPLE_BLOCK, _BLOCK_GROUP, _BLOCK = 0xA3, 0xA0, 0xA1


def text(url, languages=("en",)):
    """The best track inside the file in one of `languages`, as cues with text.

    (language, cues) or ("", []). This is what makes a release with English
    muxed in translatable when there is nothing to download - most anime -
    and the result is in time by construction: these are the file's own
    lines at the file's own times.

    The index says where every line is, so only those places are read:
    Fight Club's 1,810 lines are about 7 MB of a 1.5 GB file. A file that
    does not index its subtitle track answers nothing here.
    """
    chosen = None
    for language in languages:
        for track in tracks(url):
            if track["language"] != language or not _readable(track):
                continue
            name = track["name"].lower()
            described = "sdh" in name or name.endswith("cc") or "hearing" in name
            rank = (not described, len(track["lines"]))
            if chosen is None or rank > chosen[0]:
                chosen = (rank, track)
        if chosen is not None:
            break
    if chosen is None:
        return "", []
    track = chosen[1]
    lines = sorted(track["lines"], key=lambda line: (line[2], line[3]))
    # Five bytes in: the cluster's four-byte id and the shortest size it can
    # have. The size may be up to seven bytes longer, which `_block_at` tries.
    wanted = [(track["segment"] + cluster + relative + 5, start, end)
              for start, end, cluster, relative in lines]
    groups = []
    for item in wanted:
        if groups and item[0] - groups[-1][-1][0] <= MERGE_GAP                 and item[0] - groups[-1][0][0] <= MERGE_MAX:
            groups[-1].append(item)
        else:
            groups.append([item])
    ass = "ASS" in track["codec"].upper() or "SSA" in track["codec"].upper()

    pace = {"next": 0.0, "refused": False}
    gate = threading.Lock()

    def read(start, end):
        if pace["refused"]:
            return b""
        with gate:
            wait = pace["next"] - time.time()
            pace["next"] = max(time.time(), pace["next"]) + PACE
        if wait > 0:
            time.sleep(wait)
        data = hasher.read_range(url, start, end)
        if data is None:
            pace["refused"] = True
            return b""
        return data

    def fetch(group):
        first = group[0][0]
        data = read(first, group[-1][0] + WINDOW + 8)
        found = []
        for position, start, end in group:
            payload, longer = _block_at(data, position - first, track["number"])
            if payload is None and longer:
                more = read(position, position + LARGE_WINDOW)
                payload, _longer = _block_at(more, 0, track["number"])
            words = _spoken(payload, ass) if payload else ""
            if words:
                found.append((start, end, words))
        return found

    # The first group on its own, before the rest are asked for: a track this
    # cannot read - compressed, or laid out some way it does not know - must
    # cost one request and not several hundred.
    opening = fetch(groups[0])
    if not opening:
        kodi.log("the %s track inside the file could not be read"
                 % track["language"])
        return "", []
    answers = http.run_parallel(
        [("lines %d" % number, lambda group=group: fetch(group))
         for number, group in enumerate(groups[1:], 1)],
        workers=WORKERS, deadline=TEXT_DEADLINE)
    read = sorted(opening + [line for found in answers.values() for line in found or []])
    if len(read) < MIN_READ * len(lines):
        kodi.log("the %s track inside the file gave %d of its %d lines; not used"
                 % (track["language"], len(read), len(lines)))
        return "", []
    kodi.log("read the %s track inside the file: %d lines in %d requests"
             % (track["language"], len(read), len(groups)))
    return track["language"], [srt.Cue(number, start, end, words)
                               for number, (start, end, words) in enumerate(read, 1)]


def _readable(track):
    """Could `text` read this track? A full text track, indexed line by line
    with positions, and an episode's worth of lines rather than a film's."""
    return (track["text"] and not track["partial"]
            and MIN_LINES <= len(track["lines"]) <= MAX_LINES
            and not any(cluster is None or relative is None
                        for _s, _e, cluster, relative in track["lines"]))


def readable_languages(url):
    """The languages `text` could give for this file, for the picker to know
    a release can be translated from before anybody presses it."""
    found = []
    for track in tracks(url):
        if _readable(track) and track["language"] not in found:
            found.append(track["language"])
    return found


def _block_at(data, offset, track_number):
    """(frame, longer): the frame of the block that starts at `offset`, give
    or take the cluster's size field, or None - and whether a block was
    there but ran past the bytes in hand, which is the one case worth
    reading again for."""
    longer = False
    for extra in range(8):
        found = matroska._element(data, offset + extra)
        if found is None:
            continue
        ident, size, start, end = found
        if size is None:
            continue
        if end > len(data):
            longer = longer or (ident in (_SIMPLE_BLOCK, _BLOCK_GROUP)
                                and size <= LARGE_WINDOW)
            continue
        if ident == _SIMPLE_BLOCK:
            payload = _frame(data[start:end], track_number)
        elif ident == _BLOCK_GROUP:
            payload = None
            for child, raw, _cut in matroska._children(data, start, end):
                if child == _BLOCK:
                    payload = _frame(raw, track_number)
        else:
            continue
        if payload is not None:
            return payload, False
    return None, longer


def _frame(block, track_number):
    """A block's frame when the block is this track's, else None."""
    number, after = matroska._vint(block, 0, False)
    if number != track_number or after + 3 > len(block):
        return None
    return block[after + 3:]          # two bytes of timestamp, one of flags


def _spoken(payload, ass):
    """A frame as the words on screen."""
    words = bytes(payload).decode("utf-8", "replace").replace("\x00", "")
    if ass:
        # ReadOrder, Layer, Style, Name, MarginL, MarginR, MarginV, Effect, Text
        fields = words.split(",", 8)
        if len(fields) < 9:
            return ""
        words = fields[8]
        if "\\p1" in words or "\\p2" in words:
            return ""                 # a drawing, not a line
        words = _ASS_TAGS.sub("", words).replace("\\N", "\n").replace("\\n", "\n")
        words = words.replace("\\h", " ")
    words = _MARKUP.sub("", words)
    return "\n".join(line.strip() for line in words.splitlines() if line.strip())
