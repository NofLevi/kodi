# -*- coding: utf-8 -*-
"""The Stremio adapter, which is how three of the four providers speak.

Torrentio, Comet and MediaFusion all answer this protocol, so everything the
add-on knows about a torrent starts as one of these dictionaries. They come
from other people's servers, and the tests below are mostly about what happens
when one of them is not shaped the way the last one was.
"""
from katan.sources.providers import stremio

HASH_A = "a" * 40
HASH_B = "b" * 40


def stream(**kwargs):
    payload = {"name": "Torrentio\n1080p", "title": "A.Film.2024.1080p.WEB-DL-X",
               "infoHash": HASH_A}
    payload.update(kwargs)
    return payload


# --------------------------------------------------------------------------
# the id sent to the service
# --------------------------------------------------------------------------


def test_a_film_is_asked_for_by_imdb_id():
    assert stremio.stream_id({"type": "movie", "ids": {"imdb": "tt0111161"}}) == \
        ("movie", "tt0111161")


def test_an_episode_carries_its_season_and_number():
    assert stremio.stream_id({"type": "episode", "ids": {"imdb": "tt0944947"},
                              "season": 2, "episode": 9}) == \
        ("series", "tt0944947:2:9")


def test_anime_without_an_imdb_entry_falls_back_to_kitsu():
    """Seasonal anime often has no IMDb id at all, which is why this exists."""
    assert stremio.stream_id({"type": "episode", "ids": {"kitsu": 1234},
                              "episode": 3}) == ("series", "kitsu:1234:3")


def test_a_title_with_no_usable_id_is_not_asked_for():
    assert stremio.stream_id({"type": "movie", "ids": {}}) == ("", "")


# --------------------------------------------------------------------------
# reading one stream
# --------------------------------------------------------------------------


def test_the_release_name_comes_from_the_filename_when_there_is_one():
    source = stremio._parse_stream(
        stream(behaviorHints={"filename": "A.Film.2024.2160p.BluRay-REAL.mkv"}),
        "torrentio")
    assert source["quality"] == "2160p"
    assert source["file_name"] == "A.Film.2024.2160p.BluRay-REAL.mkv"


def test_the_stat_line_is_not_mistaken_for_the_release_name():
    source = stremio._parse_stream(
        stream(title=u"\U0001F464 42 \U0001F4BE 5.4 GB\nA.Film.2024.1080p.WEB-DL-X"),
        "torrentio")
    assert source["title"] == "A.Film.2024.1080p.WEB-DL-X"
    assert source["seeders"] == 42
    assert source["size"] == int(5.4 * 1024 ** 3)


def test_a_cache_tag_names_the_service_that_holds_it():
    source = stremio._parse_stream(stream(name="Torrentio [TB+]"), "torrentio")
    assert source["cached"] is True
    assert source["cached_by"] == "torbox"


def test_a_direct_link_means_the_debrid_copy_is_ready():
    source = stremio._parse_stream(
        stream(infoHash=None, url="https://cdn.example/file.mkv"), "comet")
    assert source["cached"] is True


def test_a_stream_with_neither_a_hash_nor_a_link_is_no_use():
    assert stremio._parse_stream(stream(infoHash=None, url=""), "comet") is None


def test_a_magnet_in_the_url_field_is_read_as_a_hash_not_as_a_link():
    """Some services put a magnet where the direct link goes.

    It is still a usable source - the hash is right there in it - but it is
    not something Kodi can play, so it must not be recorded as a stream URL
    or marked as already cached.
    """
    source = stremio._parse_stream(
        stream(infoHash=None, url="magnet:?xt=urn:btih:%s" % HASH_B), "comet")
    assert source["hash"] == HASH_B
    assert source["url"] == ""
    assert not source.get("cached")


# --------------------------------------------------------------------------
# payloads that are not shaped the way the last one was
# --------------------------------------------------------------------------


def test_one_unreadable_stream_costs_only_itself():
    """A hundred and ninety-nine good sources must not be lost to one bad one.

    These payloads are other people's, and their shapes drift. This used to
    abort the loop, turning a cosmetic upstream change into "no sources
    found".
    """
    streams = [stream(infoHash="%040d" % n) for n in range(5)]
    streams[2] = {"title": [], "name": None}          # not what anyone expects
    parsed = stremio.parse_streams(streams, "torrentio")
    assert len(parsed) == 4


def test_a_stream_that_is_not_even_a_dictionary_is_skipped():
    assert stremio.parse_streams(["nonsense", None, stream()], "torrentio")


def test_a_size_sent_as_a_string_is_still_a_size():
    source = stremio._parse_stream(
        stream(title="A.Film.2024.1080p.WEB-DL-X",
               behaviorHints={"videoSize": "5368709120"}), "torrentio")
    assert source["size"] == 5368709120


def test_a_size_sent_as_a_float_is_rounded_not_refused():
    source = stremio._parse_stream(
        stream(title="A.Film.2024.1080p.WEB-DL-X",
               behaviorHints={"videoSize": 5368709120.7}), "torrentio")
    assert source["size"] == 5368709120


def test_a_size_that_is_not_a_number_is_simply_unknown():
    source = stremio._parse_stream(
        stream(title="A.Film.2024.1080p.WEB-DL-X",
               behaviorHints={"videoSize": "unknown"}), "torrentio")
    assert source["size"] == 0, "unknown, not a crash and not a wrong number"


def test_a_file_index_that_is_not_a_number_is_ignored():
    """Better no file index than one that picks the wrong file from a pack."""
    source = stremio._parse_stream(stream(fileIdx="not a number"), "torrentio")
    assert source["file_index"] is None


def test_file_index_zero_is_kept():
    """Zero is a real index, and the falsy-check bug is easy to reintroduce."""
    source = stremio._parse_stream(stream(fileIdx=0), "torrentio")
    assert source["file_index"] == 0
