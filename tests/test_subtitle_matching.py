"""Candidate scoring decides which single subtitle gets downloaded."""
import struct

import pytest

from katan.subs import hasher, matcher


TARGET = {
    "release": "Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1.Atmos.H.264-FLUX",
    "group": "flux",
    "source": "web",
    "resolution": "1080p",
    "codec": "h264",
    "type": "movie",
}


def candidate(name, **kwargs):
    entry = {"release": name, "language": kwargs.pop("language", "en")}
    entry.update(kwargs)
    return entry


def test_a_hash_match_scores_top():
    entry = candidate("Anything.At.All.mkv", moviehash="abc123")
    assert matcher.score_candidate(entry, TARGET, video_hash="abc123") == 100
    assert entry["reason"] == "hash"


def test_an_identical_release_name_scores_top():
    entry = candidate(TARGET["release"] + ".srt")
    assert matcher.score_candidate(entry, TARGET) == 100


def test_the_same_release_group_beats_a_different_one():
    same = candidate("Dune.Part.Two.2024.2160p.WEB-DL.H265-FLUX")
    other = candidate("Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1.H264-NTB")
    assert matcher.score_candidate(same, TARGET) > matcher.score_candidate(other, TARGET)


def test_matching_source_and_resolution_add_up():
    close = candidate("Dune.Part.Two.2024.1080p.WEB-DL.H264-OTHER")
    far = candidate("Dune.Part.Two.2024.720p.HDTV.XviD-OTHER")
    assert matcher.score_candidate(close, TARGET) > matcher.score_candidate(far, TARGET)


def test_the_wrong_episode_is_pushed_to_the_bottom():
    target = dict(TARGET, type="episode", season=2, episode=7,
                  release="Show.S02E07.1080p.WEB-DL.H264-FLUX")
    right = candidate("Show.S02E07.1080p.WEB-DL.H264-FLUX")
    wrong = candidate("Show.S02E08.1080p.WEB-DL.H264-FLUX")
    assert matcher.score_candidate(right, target) > 50
    assert matcher.score_candidate(wrong, target) == 0
    assert "wrong episode" in wrong["reason"]


def test_provider_sync_percentage_contributes():
    without = candidate("Some.Other.Release.1080p.WEB-X")
    with_sync = candidate("Some.Other.Release.1080p.WEB-X", sync_percent=100)
    assert matcher.score_candidate(with_sync, TARGET) > matcher.score_candidate(without, TARGET)


def test_ranking_respects_the_language_order():
    candidates = [
        candidate("Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX", language="en"),
        candidate("Totally.Different.Release-XYZ", language="he"),
    ]
    ranked = matcher.rank(candidates, TARGET, languages=["he", "en"])
    assert ranked[0]["language"] == "he", "Hebrew is wanted first even if weaker"


def test_best_returns_one_winner_per_language():
    candidates = [
        candidate("Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1.Atmos.H.264-FLUX", language="he"),
        candidate("Dune.Part.Two.2024.720p.HDTV-OTHER", language="he"),
        candidate("Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX", language="en"),
    ]
    winners, _ = matcher.best(candidates, TARGET, threshold=70, languages=["he", "en"])
    assert set(winners) == {"he", "en"}
    assert winners["he"]["accepted"] is True
    assert winners["he"]["score"] == 100


def test_best_marks_a_weak_match_as_not_accepted():
    candidates = [candidate("Unrelated.Movie.2011.DVDRip-XVID", language="he")]
    winners, _ = matcher.best(candidates, TARGET, threshold=70, languages=["he"])
    assert winners["he"]["accepted"] is False


def test_target_is_derived_from_what_is_playing():
    target = matcher.target_from(
        {"type": "episode", "season": 1, "episode": 4},
        {"release": "Show.S01E04.1080p.WEB-DL.H264-NTB", "group": "ntb",
         "quality": "1080p"})
    assert target["group"] == "ntb"
    assert target["resolution"] == "1080p"
    assert target["source"] == "web"


# --------------------------------------------------------------------------
# the file hash
# --------------------------------------------------------------------------


def test_hash_matches_the_published_algorithm():
    """Size plus the sum of the first and last 64 KB as 64 bit integers."""
    size = 12909756
    head = struct.pack("<8192q", *([1] * 8192))
    tail = struct.pack("<8192q", *([2] * 8192))
    expected = (size + 8192 * 1 + 8192 * 2) & hasher.MASK64
    assert hasher.compute(size, head, tail) == "%016x" % expected


def test_hash_is_sixteen_hex_characters():
    head = b"\x00" * hasher.CHUNK
    tail = b"\xff" * hasher.CHUNK
    value = hasher.compute(1234567, head, tail)
    assert len(value) == 16
    assert all(c in "0123456789abcdef" for c in value)


def test_hash_of_a_missing_file_is_empty():
    assert hasher.hash_local("does-not-exist.mkv") == ("", 0)


def test_hash_stream_refuses_a_non_http_path(no_network):
    assert hasher.hash_stream("") == ("", 0)
    assert hasher.hash_stream("/local/path.mkv") == ("", 0)


# --------------------------------------------------------------------------
# what we are trying to match
# --------------------------------------------------------------------------


def test_the_file_that_is_playing_beats_the_torrent_it_came_in():
    """For a season pack these are different strings, and only one of them
    names the episode.

    A pack is "Silo.S01.COMPLETE.1080p.WEB-DL-GRP" and carries no episode
    number at all, so matching against it threw away the strongest signal
    there is - and every episode of that pack was matched against the same
    text, so one subtitle looked equally good for all ten.
    """
    target = matcher.target_from(
        {"type": "episode", "season": 1, "episode": 3},
        {"release": "Silo.S01.COMPLETE.1080p.WEB-DL-GRP",
         "file_name": "Silo.S01E03.Machines.1080p.WEB-DL-GRP.mkv",
         "group": "GRP", "quality": "1080p"})
    assert "S01E03" in target["release"]


def test_the_torrent_name_is_used_when_the_file_is_not_known():
    """Not every service tells us which file it opened."""
    target = matcher.target_from(
        {"type": "movie"},
        {"release": "A.Film.2020.1080p.WEB-DL-GRP", "file_name": "",
         "group": "GRP", "quality": "1080p"})
    assert target["release"] == "A.Film.2020.1080p.WEB-DL-GRP"


def test_an_episode_file_scores_above_the_pack_it_came_in():
    """End to end: the subtitle written for episode three should win."""
    pack = matcher.target_from(
        {"type": "episode", "season": 1, "episode": 3},
        {"release": "Silo.S01.COMPLETE.1080p.WEB-DL-GRP", "file_name": "",
         "group": "GRP", "quality": "1080p"})
    episode = matcher.target_from(
        {"type": "episode", "season": 1, "episode": 3},
        {"release": "Silo.S01.COMPLETE.1080p.WEB-DL-GRP",
         "file_name": "Silo.S01E03.Machines.1080p.WEB-DL-GRP.mkv",
         "group": "GRP", "quality": "1080p"})

    candidate = {"release": "Silo.S01E03.Machines.1080p.WEB-DL-GRP",
                 "language": "he"}
    against_pack = matcher.score_candidate(dict(candidate), pack)
    against_episode = matcher.score_candidate(dict(candidate), episode)

    assert against_episode > against_pack, (
        "the subtitle written for this exact episode should score higher "
        "against the episode file: %d against %d"
        % (against_episode, against_pack))
    assert against_episode == 100, "identical release name is a certainty"
