"""Subtitle archives are untrusted small downloads with potentially huge output."""
import gzip
import io
import zipfile

import pytest

from katan import urlsession
from katan.subs.providers import bsplayer, common, opensubtitles_rest, wizdom


def test_archive_language_matching_uses_tokens_not_substrings():
    names = ["The.Movie.English.srt", "The.Movie.he.srt"]
    assert common._pick_from_archive(names, "he") == "The.Movie.he.srt"


def test_archive_language_aliases_cover_supported_catalogue_codes():
    cases = {
        "fr": "movie.fre.srt", "de": "movie.deu.srt",
        "nl": "movie.nld.srt", "ro": "movie.ron.srt",
        "cs": "movie.ces.srt", "zh": "movie.zho.srt",
        "ja": "movie.jpn.srt", "ru": "movie.rus.srt",
    }
    decoy = "movie.eng.srt"
    for language, expected in cases.items():
        assert common._pick_from_archive([decoy, expected], language) == expected


def test_ambiguous_archive_without_requested_language_is_rejected():
    names = ["movie.eng.srt", "movie.deu.srt"]
    assert common._pick_from_archive(names, "fr") == ""


@pytest.mark.parametrize("name", ["Film.he.forced.srt", "Film.he.sdh.srt",
                                        "Film.he.cc.srt", "Film.he.default.srt"])
def test_archive_language_code_before_modifier_is_recognized(name):
    assert common._pick_from_archive([name, "Film.en.srt"], "he") == name


def test_archive_matches_language_before_preferring_srt_format():
    names = ["movie.eng.srt", "movie.heb.ass"]
    assert common._pick_from_archive(names, "he") == "movie.heb.ass"


def test_short_language_alias_in_title_is_not_a_tag():
    names = ["He.2024.deu.srt", "He.2024.eng.srt"]
    assert common._pick_from_archive(names, "he") == ""


def test_multipart_requested_language_archive_is_ambiguous():
    names = ["movie.part1.he.srt", "movie.part2.he.srt"]
    assert common._pick_from_archive(names, "he") == ""


def test_webvtt_archive_entry_can_be_saved_as_srt(tmp_path):
    data = b"WEBVTT\n\n00:01.000 --> 00:03.000\nHello\n\n"
    path = common.save(data, str(tmp_path), "converted.srt")
    assert path
    with open(path, "rb") as handle:
        converted = handle.read()
    assert b"00:00:01,000 --> 00:00:03,000" in converted


def test_compressed_archive_cannot_expand_past_subtitle_limit():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("movie.he.srt", b"x" * (common.MAX_DOWNLOAD_BYTES + 1))
    assert len(buffer.getvalue()) < common.MAX_DOWNLOAD_BYTES
    assert common.extract_subtitle(buffer.getvalue(), "he") == b""


def test_matching_archives_receive_a_shared_provenance_fingerprint():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("movie.en.srt", b"subtitle")
    left, right = {}, {}

    common.extract_subtitle(buffer.getvalue(), "en", candidate=left)
    common.extract_subtitle(buffer.getvalue(), "en", candidate=right)

    assert left["archive_fingerprint"] == right["archive_fingerprint"]
    assert len(left["archive_fingerprint"]) == 64


def test_provider_download_attaches_archive_provenance(monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("movie.he.srt", b"subtitle")
    monkeypatch.setattr(wizdom.common, "fetch_bytes",
                        lambda url, **kwargs: buffer.getvalue())
    candidate = {"download": "https://example.invalid/archive.zip",
                 "language": "he"}

    assert wizdom.download(candidate) == b"subtitle"
    assert len(candidate["archive_fingerprint"]) == 64


def test_http_gzip_is_bounded_before_standard_session_decoding(monkeypatch):
    packed = gzip.compress(b"x" * (common.MAX_DOWNLOAD_BYTES + 1))
    response = urlsession.Response(
        "https://example.invalid/subtitle", 200,
        {"Content-Encoding": "gzip"}, packed)
    monkeypatch.setattr(common.http, "get", lambda *args, **kwargs: response)

    assert common.fetch_bytes("https://example.invalid/subtitle") == b""
    assert response._content is None


def test_http_deflate_is_decoded_within_the_subtitle_limit(monkeypatch):
    import zlib
    payload = b"subtitle text"
    response = urlsession.Response(
        "https://example.invalid/subtitle", 200,
        {"Content-Encoding": "deflate"}, zlib.compress(payload))
    monkeypatch.setattr(common.http, "get", lambda *args, **kwargs: response)

    assert common.fetch_bytes("https://example.invalid/subtitle") == payload
    assert response._content is None


def test_http_deflate_expansion_is_bounded(monkeypatch):
    import zlib
    packed = zlib.compress(b"x" * (common.MAX_DOWNLOAD_BYTES + 1))
    response = urlsession.Response(
        "https://example.invalid/subtitle", 200,
        {"Content-Encoding": "deflate"}, packed)
    monkeypatch.setattr(common.http, "get", lambda *args, **kwargs: response)

    assert common.fetch_bytes("https://example.invalid/subtitle") == b""
    assert response._content is None


def test_corrupt_gzip_fails_closed():
    payload = b"subtitle line " * 1000
    packed = bytearray(gzip.compress(payload, compresslevel=9))
    packed[10] ^= 1

    assert common.decompress_gzip(bytes(packed)) == b""


def test_deflate_rejects_trailing_bytes():
    import zlib
    payload = b"subtitle text"
    wrapped = zlib.compress(payload) + b"junk"
    encoder = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    raw = encoder.compress(payload) + encoder.flush() + b"junk"

    assert common.decompress_deflate(wrapped) == b""
    assert common.decompress_deflate(raw) == b""


def test_http_x_gzip_is_accepted(monkeypatch):
    payload = b"subtitle text"
    response = urlsession.Response(
        "https://example.invalid/subtitle", 200,
        {"Content-Encoding": "x-gzip"}, gzip.compress(payload))
    monkeypatch.setattr(common.http, "get", lambda *args, **kwargs: response)
    assert common.fetch_bytes("https://example.invalid/subtitle") == payload


def test_http_raw_deflate_is_accepted(monkeypatch):
    import zlib
    payload = b"subtitle text"
    encoder = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    packed = encoder.compress(payload) + encoder.flush()
    response = urlsession.Response(
        "https://example.invalid/subtitle", 200,
        {"Content-Encoding": "deflate"}, packed)
    monkeypatch.setattr(common.http, "get", lambda *args, **kwargs: response)
    assert common.fetch_bytes("https://example.invalid/subtitle") == payload


def test_gzip_boundary_does_not_triple_peak_memory():
    import gc
    import tracemalloc
    packed = gzip.compress(b"x" * common.MAX_DOWNLOAD_BYTES)
    gc.collect()
    tracemalloc.start()
    try:
        unpacked = common.decompress_gzip(packed)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(unpacked) == common.MAX_DOWNLOAD_BYTES
    assert peak < common.MAX_DOWNLOAD_BYTES * 3


def test_gzip_member_count_is_bounded():
    packed = gzip.compress(b"") * 9 + gzip.compress(b"subtitle")
    assert common.decompress_gzip(packed) == b""


def test_gzip_stream_completion_semantics():
    first = gzip.compress(b"first")
    second = gzip.compress(b"second")
    assert common.decompress_gzip(first + second) == b"firstsecond"
    assert common.decompress_gzip(first[:-2]) == b""

    corrupt_second = bytearray(gzip.compress(b"subtitle line " * 1000,
                                             compresslevel=9))
    corrupt_second[10] ^= 1
    assert common.decompress_gzip(first + bytes(corrupt_second)) == b""


def test_deflate_boundaries_and_truncation():
    import zlib
    exact = zlib.compress(b"x" * common.MAX_DOWNLOAD_BYTES)
    over = zlib.compress(b"x" * (common.MAX_DOWNLOAD_BYTES + 1))
    assert len(common.decompress_deflate(exact)) == common.MAX_DOWNLOAD_BYTES
    assert common.decompress_deflate(over) == b""
    assert common.decompress_deflate(exact[:-2]) == b""


def test_bsplayer_gzip_cannot_expand_past_subtitle_limit(monkeypatch):
    packed = gzip.compress(b"x" * (common.MAX_DOWNLOAD_BYTES + 1))
    monkeypatch.setattr(bsplayer.common, "fetch_bytes",
                        lambda url, **kwargs: packed)
    candidate = {"download": "https://example.invalid/sub.gz", "language": "he"}
    assert bsplayer.download(candidate) == b""


def test_opensubtitles_gzip_cannot_expand_past_subtitle_limit(monkeypatch):
    packed = gzip.compress(b"x" * (common.MAX_DOWNLOAD_BYTES + 1))
    monkeypatch.setattr(opensubtitles_rest.common, "fetch_bytes",
                        lambda url, **kwargs: packed)
    candidate = {"download": "https://example.invalid/sub.gz", "language": "he"}
    assert opensubtitles_rest.download(candidate) == b""
