# -*- coding: utf-8 -*-
"""Reading the subtitle tracks an MKV declares, from its first bytes.

Built byte for byte from the Matroska element layout rather than taken from
a file, so every case is exactly the shape it claims to be. Measured live
before this was written: twelve cached anime releases, every one with a
full English track inside.
"""
from pinky.utils import matroska


def _size(n):
    return b"\x01" + n.to_bytes(7, "big")


def _el(ident, payload=b""):
    width = (ident.bit_length() + 7) // 8
    return ident.to_bytes(width, "big") + _size(len(payload)) + payload


def _track(kind=0x11, codec=b"S_TEXT/ASS", language=b"eng", name=b"",
           forced=None, bcp47=None):
    body = _el(0x83, bytes([kind])) + _el(0x86, codec)
    if language is not None:
        body += _el(0x22B59C, language)
    if bcp47 is not None:
        body += _el(0x22B59D, bcp47)
    if name:
        body += _el(0x536E, name)
    if forced is not None:
        body += _el(0x55AA, bytes([forced]))
    return _el(0xAE, body)


def _mkv(*tracks, before=b"", after=b""):
    tracks_element = _el(0x1654AE6B, b"".join(tracks))
    segment = _el(0x18538067, before + tracks_element + after)
    return _el(0x1A45DFA3, _el(0x4286, b"\x01")) + segment


def test_a_full_english_track_and_a_signs_track():
    data = _mkv(_track(kind=1, codec=b"V_MPEG4/ISO/AVC"),
                _track(kind=2, codec=b"A_AAC", language=b"jpn"),
                _track(name=b"English"),
                _track(name=b"English (Signs & Songs)"))
    tracks = matroska.subtitle_tracks(data)
    assert [(t["language"], t["partial"]) for t in tracks] == \
        [("en", False), ("en", True)]


def test_a_track_naming_no_language_is_english_by_the_specification():
    tracks = matroska.subtitle_tracks(_mkv(_track(language=None)))
    assert tracks[0]["language"] == "en"


def test_hebrew_and_the_newer_language_tag():
    tracks = matroska.subtitle_tracks(_mkv(
        _track(language=b"heb"), _track(language=b"und", bcp47=b"ar-SA")))
    assert [t["language"] for t in tracks] == ["he", "ar"]


def test_a_forced_track_is_partial():
    tracks = matroska.subtitle_tracks(_mkv(_track(forced=1)))
    assert tracks[0]["partial"] is True


def test_a_file_that_is_not_matroska_is_unknown_not_empty():
    assert matroska.subtitle_tracks(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64) is None
    assert matroska.subtitle_tracks(b"") is None


def test_a_header_cut_off_before_its_tracks_end_is_unknown():
    data = _mkv(_track(name=b"English"))
    assert matroska.subtitle_tracks(data[:-5]) is None


def test_a_cluster_before_the_tracks_means_the_tracks_are_not_here():
    cluster = _el(0x1F43B675, b"\x00" * 16)
    assert matroska.subtitle_tracks(_mkv(_track(), before=cluster)) is None


def test_elements_before_the_tracks_are_stepped_over():
    info = _el(0x1549A966, b"\x00" * 300)
    void = _el(0xEC, b"\x00" * 1000)
    tracks = matroska.subtitle_tracks(_mkv(_track(), before=info + void))
    assert tracks and tracks[0]["language"] == "en"


def test_a_file_with_no_subtitle_track_is_known_to_have_none():
    data = _mkv(_track(kind=1, codec=b"V_MPEGH/ISO/HEVC"))
    assert matroska.subtitle_tracks(data) == []


def test_a_segment_of_unknown_size():
    tracks_element = _el(0x1654AE6B, _track())
    data = (_el(0x1A45DFA3, b"") + (0x18538067).to_bytes(4, "big")
            + b"\x01\xff\xff\xff\xff\xff\xff\xff" + tracks_element)
    assert matroska.subtitle_tracks(data)[0]["language"] == "en"
