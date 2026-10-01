# -*- coding: utf-8 -*-
"""The subtitle track inside the file, read from its index as a clock.

Whether a downloaded subtitle is in time used to be a judgement about its
name. Measured on the cached copy of The Invite: four Hebrew subtitles
fitting at 75% by name, every one of them 19.4 seconds out - which the
file's own Portuguese track, 2,001 lines in an 86 KB index, measured exactly.
"""
import pytest

from pinky.subs import auto, hasher, inside
from pinky.utils import matroska
from test_matroska import _el, _track as _track_entry


def _uint(value, width=None):
    width = width or max(1, (value.bit_length() + 7) // 8)
    return value.to_bytes(width, "big")


def _numbered(number, **described):
    """A track entry carrying its track number."""
    entry = _track_entry(**described)
    # Re-open the entry to put TrackNumber inside it.
    body = entry[len(b"\xae") + 8:]
    return _el(0xAE, _el(0xD7, _uint(number)) + body)


def _cue(start, track, duration=None, cluster=1000, relative=20):
    position = _el(0xF7, _uint(track)) + _el(0xF1, _uint(cluster))
    if relative is not None:
        position += _el(0xF0, _uint(relative))
    if duration is not None:
        position += _el(0xB2, _uint(duration))
    return _el(0xBB, _el(0xB3, _uint(start)) + _el(0xB7, position))


def _file(tracks, cues, clusters=b""):
    """SeekHead -> Cues, Info, Tracks, any clusters, then the Cues."""
    info = _el(0x1549A966, _el(0x2AD7B1, _uint(1000000)))
    tracks_element = _el(0x1654AE6B, b"".join(tracks))
    cues_element = _el(0x1C53BB6B, b"".join(cues))
    cues_at = _before_clusters(tracks) + len(clusters)
    seek_head = _el(0x114D9B74, _el(0x4DBB, _el(0x53AB, _uint(0x1C53BB6B, 4))
                                    + _el(0x53AC, _uint(cues_at, 4))))
    payload = seek_head + info + tracks_element + clusters + cues_element
    return _el(0x1A45DFA3, _el(0x4286, b"\x01")) + _el(0x18538067, payload)


def _before_clusters(tracks):
    """How far into the Segment the first cluster starts."""
    seek_size = len(_el(0x114D9B74, _el(0x4DBB, _el(0x53AB, _uint(0x1C53BB6B, 4))
                                         + _el(0x53AC, _uint(0, 4)))))
    info = _el(0x1549A966, _el(0x2AD7B1, _uint(1000000)))
    return seek_size + len(info) + len(_el(0x1654AE6B, b"".join(tracks)))


def _spoken_file(track_entries, track, words, every=4000, long=2000):
    """A file whose `track` says each of `words`, one cluster a line, with
    the index pointing at every block."""
    clusters, cues = b"", []
    base = _before_clusters(track_entries)
    for number, line in enumerate(words):
        block = _el(0xA0, _el(0xA1, bytes([0x80 | track]) + b"\x00\x00\x00" + line)
                    + _el(0x9B, _uint(long)))
        stamp = _el(0xE7, _uint(number * every))
        video = _el(0xA3, b"\x81\x00\x00\x80" + b"\x00" * 300)
        cues.append(_cue(number * every, track, duration=long,
                         cluster=base + len(clusters), relative=len(stamp) + len(video)))
        clusters += _el(0x1F43B675, stamp + video + block)
    return _file(track_entries, cues, clusters)


@pytest.fixture
def remote(monkeypatch):
    """Serve ranged reads out of a byte string, as the debrid CDN would."""
    held = {}

    def read_range(url, start, end, timeout=None):
        data = held.get(url)
        return None if data is None else data[start:end + 1]

    monkeypatch.setattr(hasher, "read_range", read_range)
    inside._HELD.clear()
    return held


def _lines(track, count, every=4000, long=2000, offset=0):
    return [_cue(offset + n * every, track, duration=long) for n in range(count)]


def test_the_index_times_every_line_of_a_subtitle_track():
    data = _file([_numbered(1, kind=1, codec=b"V_MPEG4/ISO/AVC"),
                  _numbered(3, name=b"English")],
                 [_cue(0, 1), _cue(1000, 3, duration=2500), _cue(5000, 3, duration=1500)])
    where = matroska.layout(data)
    assert [(t["number"], t["language"], t["text"]) for t in where["tracks"]] == [(3, "en", True)]
    body_at = where["cues"]
    element = matroska._element(data, body_at)
    index = matroska.cue_index(data[element[2]:element[2] + element[1]], where["scale"])
    # The video's own cue points have no duration, and time nothing.
    assert sorted(index) == [3]
    assert [(round(a, 3), round(b, 3)) for a, b, _c, _r in index[3]] == [(1.0, 3.5), (5.0, 6.5)]
    assert index[3][0][2:] == (1000, 20)


def test_the_fullest_dialogue_track_is_the_ruler(remote):
    remote["u"] = _file(
        [_numbered(2, name=b"Signs & Songs"), _numbered(3, name=b"English [SDH]"),
         _numbered(4, language=b"por")],
        _lines(2, 60) + _lines(3, 90) + _lines(4, 70))
    ruler = inside.timeline("u")
    # Not the signs track, and not the one that also times the sound effects.
    assert len(ruler) == 70
    assert (ruler[0].start, ruler[0].end, ruler[0].text) == (0.0, 2.0, "")
    assert len(inside.timeline("u", language="en")) == 90


def test_a_few_lines_are_not_a_ruler(remote):
    """A forced track by another name: correlating dialogue against it would
    refuse a good subtitle."""
    remote["u"] = _file([_numbered(3, name=b"English")], _lines(3, 12))
    assert inside.timeline("u") == []


def test_a_file_that_indexes_only_its_video_has_no_ruler(remote):
    """An older fansub mux: the track is there and the index does not time it."""
    remote["u"] = _file([_numbered(1, kind=1, codec=b"V_MPEG4/ISO/AVC"), _numbered(3)],
                        [_cue(n * 1000, 1) for n in range(200)])
    assert inside.tracks("u") == []
    assert inside.timeline("u") == []


def test_what_is_not_matroska_or_cannot_be_read_is_no_ruler(remote):
    remote["mp4"] = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200
    assert inside.timeline("mp4") == []
    assert inside.timeline("nowhere") == []
    assert inside.timeline("") == []


def test_the_files_own_timeline_is_the_first_reference(remote):
    """Ahead of a hash match: this is the cut by definition, where a hash is
    the cut by somebody's registration of it."""
    remote["u"] = _file([_numbered(3, name=b"English")], _lines(3, 80))

    class Budget(object):
        ruler = None

        def fetch(self, candidate, *args, **kwargs):
            raise AssertionError("nothing should be downloaded for a ruler the file has")

    downloads = Budget()
    downloads.ruler = auto.ruler_later({"stream_url": "u"})
    winners = {"en": {"reason": "hash", "language": "en"}}
    assert len(auto.reference_cues(winners, ["he", "en"], downloads)) == 80
    assert len(auto.hash_reference([{"reason": "hash"}], downloads)) == 80


def test_no_stream_address_means_no_ruler_and_no_waiting():
    assert auto.ruler_later({})() == []


def _dialogue(count):
    return [("Line %d of the dialogue." % number).encode("utf-8") for number in range(count)]


def test_the_lines_are_read_out_of_the_file_where_the_index_says(remote):
    """Naruto Shippuden 3x55 drew "No Hebrew found" beside a file with 334
    lines of English in it, because a translation needed a download."""
    entries = [_numbered(1, kind=1, codec=b"V_MPEG4/ISO/AVC"),
               _numbered(3, codec=b"S_TEXT/UTF8", name=b"English")]
    remote["u"] = _spoken_file(entries, 3, _dialogue(60))
    language, cues = inside.text("u", ("ar", "en"))
    assert language == "en" and len(cues) == 60
    assert (cues[0].start, cues[0].end, cues[0].text) == (0.0, 2.0, "Line 0 of the dialogue.")
    assert cues[59].text == "Line 59 of the dialogue." and cues[59].start == 236.0
    assert inside.readable_languages("u") == ["en"]


def test_an_ass_line_is_its_words(remote):
    entries = [_numbered(3, codec=b"S_TEXT/ASS")]
    words = [b"%d,0,Default,,0000,0000,0000,,{\\i1}People always ask me{\\i0}\\Nif I know Tyler." % n
             for n in range(45)]
    words[7] = b"7,0,Default,,0000,0000,0000,,{\\p1}m 0 0 l 100 0 100 100{\\p0}"
    remote["u"] = _spoken_file(entries, 3, words)
    _language, cues = inside.text("u", ("en",))
    assert cues[0].text == "People always ask me\nif I know Tyler."
    assert len(cues) == 44, "a drawing is not a line"


def test_a_films_worth_of_lines_is_not_read_line_by_line(remote):
    """Each line is a request, and 3,167 of them in 38 seconds had the debrid
    CDN answering 429 to everything for minutes - the player included."""
    entries = [_numbered(3, codec=b"S_TEXT/UTF8")]
    remote["u"] = _file(entries, _lines(3, inside.MAX_LINES + 1))
    assert inside.readable_languages("u") == []
    assert inside.text("u", ("en",)) == ("", [])
    assert len(inside.timeline("u")) == inside.MAX_LINES + 1, "it is still a ruler"


def test_a_refusal_ends_the_read(remote, monkeypatch):
    entries = [_numbered(3, codec=b"S_TEXT/UTF8")]
    data = _spoken_file(entries, 3, _dialogue(120))
    remote["u"] = data
    assert inside.readable_languages("u") == ["en"]       # the index, while it answers
    asked = []

    def refusing(url, start, end, timeout=None):
        asked.append(start)
        return data[start:end + 1] if len(asked) <= 3 else None

    monkeypatch.setattr(hasher, "read_range", refusing)
    monkeypatch.setattr(inside, "MERGE_GAP", 0)            # one request a line
    monkeypatch.setattr(inside, "PACE", 0.0)
    assert inside.text("u", ("en",)) == ("", [])
    assert len(asked) <= 3 + inside.WORKERS, "it stopped asking, not 120 times"


def test_the_files_own_track_leads_what_is_translated(remote, monkeypatch):
    entries = [_numbered(3, codec=b"S_TEXT/UTF8", name=b"English")]
    remote["u"] = _spoken_file(entries, 3, _dialogue(50))
    monkeypatch.setattr(auto, "translation_source_languages",
                        lambda meta, already=(): ["ar", "en"])
    language, candidate = auto.inside_source({"stream_url": "u"}, "he")
    assert language == "en" and candidate["inside"] and len(candidate["cues"]) == 50
    assert auto.inside_source({}, "he") is None
    assert auto.inside_source({"stream_url": "nowhere"}, "he") is None
