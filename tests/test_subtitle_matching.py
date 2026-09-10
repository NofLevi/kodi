"""Candidate scoring decides which single subtitle gets downloaded."""
import struct


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


def test_an_explicitly_different_movie_cut_is_not_accepted():
    target = matcher.target_from({
        "type": "movie",
        "source": {"release":
                   "Film.2024.Extended.1080p.BluRay.H264-GRP"}})
    wrong = candidate("Film.2024.Theatrical.1080p.BluRay.H264-GRP")

    assert matcher.score_candidate(wrong, target) < 70
    assert "different edition" in wrong["reason"]


def test_final_cut_and_directors_cut_are_different_editions():
    target = matcher.target_from({
        "type": "movie",
        "source": {"release":
                   "Blade.Runner.1982.Final.Cut.1080p.BluRay.H264-GRP"}})
    wrong = candidate(
        "Blade.Runner.1982.Directors.Cut.1080p.BluRay.H264-GRP")

    assert matcher.score_candidate(wrong, target) < 70
    assert "different edition" in wrong["reason"]


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


def test_range_server_ignoring_range_is_rejected_without_reading_body(monkeypatch):
    """A 200 response may be the whole movie and must never enter RAM."""
    class WholeMovie(object):
        status_code = 200
        headers = {"Content-Length": str(20 * 1024 ** 3)}
        closed = False
        body_read = False

        @property
        def content(self):
            self.body_read = True
            return b"x" * hasher.CHUNK

        def close(self):
            self.closed = True

    response = WholeMovie()
    monkeypatch.setattr(hasher.http, "get", lambda *args, **kwargs: response)
    assert hasher._range("https://cdn/video", 0, hasher.CHUNK - 1,
                         (1, 1)) is None
    assert response.body_read is False
    assert response.closed is True


def test_range_requires_matching_content_range_without_reading_body(monkeypatch):
    class WrongSlice(object):
        status_code = 206
        headers = {"Content-Range": "bytes 5-65540/999999"}
        closed = False
        body_read = False

        @property
        def content(self):
            self.body_read = True
            return b"x" * hasher.CHUNK

        def close(self):
            self.closed = True

    response = WrongSlice()
    monkeypatch.setattr(hasher.http, "get", lambda *args, **kwargs: response)
    assert hasher._range("https://cdn/video", 0, hasher.CHUNK - 1,
                         (1, 1)) is None
    assert response.body_read is False
    assert response.closed is True


def test_valid_range_reads_only_the_requested_chunk(monkeypatch):
    class Raw(object):
        amount = None

        def read(self, amount, decode_content=False):
            self.amount = amount
            return b"x" * hasher.CHUNK

    class Slice(object):
        status_code = 206
        headers = {"Content-Range": "bytes 0-65535/999999"}
        raw = Raw()
        closed = False

        def close(self):
            self.closed = True

    response = Slice()
    monkeypatch.setattr(hasher.http, "get", lambda *args, **kwargs: response)
    assert hasher._range("https://cdn/video", 0, hasher.CHUNK - 1,
                         (1, 1)) == b"x" * hasher.CHUNK
    assert response.raw.amount == hasher.CHUNK + 1
    assert response.closed is True


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


# --------------------------------------------------------------------------
# what the number means
#
# Pinned deliberately, because the old scale was wrong in a way no ordering
# test could see. Every candidate is the answer to a search for one specific
# title, and that - the strongest evidence there is - was worth nothing in
# the sum, so a subtitle agreeing on source, resolution *and* codec came out
# at 33%. The order was right the whole time; the number was not, and the
# number is what a viewer reads before deciding the add-on cannot find
# subtitles.
# --------------------------------------------------------------------------

CALIBRATION_TARGET = {
    "release": "The.Film.2024.1080p.BluRay.x264-AMIABLE",
    "group": "amiable", "source": "bluray", "resolution": "1080p",
    "codec": "h264", "type": "movie",
}


def scored(name, **extra):
    candidate = {"provider": "wizdom", "language": "he", "release": name}
    candidate.update(extra)
    matcher.score_candidate(candidate, CALIBRATION_TARGET)
    return candidate["score"]


def test_the_same_release_name_is_certain():
    assert scored("The.Film.2024.1080p.BluRay.x264-AMIABLE") == 100


def test_the_same_group_is_nearly_certain():
    """Groups mux their own timings, so a subtitle made for a group release
    fits that release."""
    assert scored("The.Film.2024.1080p.BluRay.x264-AMIABLE.HEB") >= 90


def test_source_resolution_and_codec_together_are_a_good_match():
    """The case that used to read 33%. It is the best a Hebrew provider can
    normally offer, because Hebrew subtitles are rarely made per group."""
    assert scored("The.Film.2024.1080p.BluRay.x264-OTHER") == 70


def test_the_same_source_alone_is_a_maybe():
    assert scored("The.Film.2024.BRRip.XviD-OTHER") == 55


def test_the_right_title_and_nothing_else_is_still_worth_something():
    """It is not nothing: this candidate was returned by a search for this
    film, which is more than can be said for a random subtitle."""
    assert scored("The Film") == 40


def test_two_unknown_resolutions_are_not_an_agreement():
    """They agree about nothing. This was worth 12 points until it was found,
    and the parser returning "unknown" rather than assuming "sd" made it fire
    far more often."""
    blind = dict(CALIBRATION_TARGET, resolution="unknown")
    candidate = {"release": "The.Film.2024.BluRay.x264-OTHER", "language": "he"}
    matcher.score_candidate(candidate, blind)
    assert "resolution" not in candidate["reason"]


def test_the_wrong_episode_stays_at_the_bottom():
    """The base credit for being the right *title* must not rescue a
    candidate that is demonstrably the wrong episode of it."""
    target = dict(CALIBRATION_TARGET, type="episode", season=1, episode=4,
                  release="Show.S01E04.1080p.BluRay.x264-AMIABLE")
    candidate = {"release": "Show.S01E09.1080p.BluRay.x264-AMIABLE",
                 "language": "he"}
    matcher.score_candidate(candidate, target)
    assert candidate["score"] == 0


def test_a_good_match_actually_clears_the_default_threshold():
    """The point of the recalibration. Under the old scale nothing a Hebrew
    provider returned could ever be accepted, so every film fell through to
    "below threshold, used anyway" and every subtitle was shown as a guess."""
    from katan import settings
    assert scored("The.Film.2024.1080p.BluRay.x264-OTHER") >= \
        settings.get_int("subs.threshold", 70)


# --------------------------------------------------------------------------
# a hash match that is somebody else's file
# --------------------------------------------------------------------------

def test_exact_hash_evidence_wins_equal_score_release_name_tie():
    target = {"release": "Film.2024.WEB-DL-GRP", "type": "movie"}
    rows = [
        {"provider": "a", "language": "en",
         "release": "Film.2024.WEB-DL-GRP", "download": "name"},
        {"provider": "b", "language": "en", "release": "other",
         "download": "hash", "hash_match": True},
    ]
    winners, ranked = matcher.best(rows, target, 70, languages=["en"])
    assert winners["en"]["reason"] == "hash"
    assert ranked[0]["download"] == "hash"


def test_identical_release_name_cannot_override_wrong_episode_metadata():
    release_name = "Show.S01E01.1080p.WEB-DL-GRP"
    target = {"release": release_name, "group": "grp", "source": "web",
              "resolution": "1080p", "codec": "h264", "editions": [],
              "type": "episode", "season": 1, "episode": 2,
              "absolute": None}
    score, reason = matcher.rate({"release": release_name}, target)
    assert score < 70
    assert "wrong episode" in reason


def test_a_hash_match_for_a_different_episode_is_not_a_hundred():
    """Measured against the live index, not imagined.

    One real Breaking Bad S01E01 file hash is registered at OpenSubtitles
    against S01E07, against The Vampire Diaries and against a Bollywood film -
    every row claiming `MatchedBy: moviehash`. `rate` returned 100 for a hash
    before the wrong-episode check ran, so the worst kind of mismatch scored
    the highest mark available and won.
    """
    from katan.subs import matcher

    target = {"type": "episode", "season": 1, "episode": 1,
              "release": "Breaking.Bad.S01E01.1080p.WEB.x264-GRP"}
    right = matcher.rate({"release": "Breaking.Bad.S01E01.720p.HDTV.x264-BiA",
                          "hash_match": True}, target)
    wrong = matcher.rate({"release": "Breaking.Bad.S01E07.HDTV.XviD-LOL.avi",
                          "hash_match": True}, target)
    assert right == (100, "hash")
    assert wrong[0] == 0, wrong


def test_a_hash_match_that_says_nothing_is_still_a_hundred():
    """Silence is not a contradiction.

    Genuine hash uploads are often named "Episode 01 - Pilot.srt" or worse,
    and treating a name that states no episode as disagreement would throw
    away most of the real hash matches there are.
    """
    from katan.subs import matcher

    target = {"type": "episode", "season": 1, "episode": 1,
              "release": "Breaking.Bad.S01E01.1080p.WEB.x264-GRP"}
    for name in ("Episode 01 - Pilot.srt", "subtitle.srt", ""):
        score, reason = matcher.rate({"release": name, "hash_match": True},
                                     target)
        assert (score, reason) == (100, "hash"), name


def test_a_film_hash_match_is_unaffected():
    from katan.subs import matcher

    target = {"type": "movie", "release": "Fight.Club.1999.1080p.BluRay-GRP"}
    assert matcher.rate({"release": "anything at all", "hash_match": True},
                        target) == (100, "hash")
