"""Candidate scoring decides which single subtitle gets downloaded."""
import pytest

import struct


from pinky.subs import hasher, matcher


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


def test_title_source_and_resolution_reach_the_threshold_on_their_own():
    """The case that used to read 33%, and then 65.

    It is the best a Hebrew provider can normally offer, because Hebrew
    subtitles are rarely made per group - and it must clear the default
    threshold of 70 without needing a codec token too. The test that stood
    here used a name with x264 on both sides, so it reached 70 through the
    codec and never noticed the rung itself added to 65.
    """
    assert scored("The.Film.2024.1080p.BluRay-OTHER") == 70


def test_a_matching_codec_is_a_bonus_on_top():
    assert scored("The.Film.2024.1080p.BluRay.x264-OTHER") == 75


def test_every_documented_rung_holds_without_a_codec():
    """The ladder in matcher.py is a promise; each rung is checked bare."""
    assert scored("The.Film.2024.1080p.BluRay-AMIABLE") == 95   # group
    assert scored("The.Film.2024.1080p.BluRay-OTHER") == 70     # title+source+res
    assert scored("The.Film.2024.BRRip-OTHER") == 55            # title+source
    assert scored("The Film") == 40                             # title


def test_only_certainty_scores_a_hundred():
    """100 means the same file or the same name, and nothing else may say it.

    Group, source, resolution and codec together sum past 100. That is very
    nearly the same release - and still not the same file, which is the only
    thing 100 is allowed to mean, because the chooser shows a bare "100%" as
    exact and the picker sorts on it.
    """
    # An audio token the matcher does not score keeps the name distinct from
    # the target. Not ".HEB": that is a subtitle language tag, stripped before
    # comparing, so it *is* the identical release name and rightly scores 100.
    nearly = scored("The.Film.2024.1080p.BluRay.DTS.x264-AMIABLE")
    assert nearly == 99
    assert scored("The.Film.2024.1080p.BluRay.x264-AMIABLE") == 100
    assert scored("The.Film.2024.1080p.BluRay.x264-AMIABLE.HEB") == 100


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
    from pinky import settings
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
    from pinky.subs import matcher

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
    from pinky.subs import matcher

    target = {"type": "episode", "season": 1, "episode": 1,
              "release": "Breaking.Bad.S01E01.1080p.WEB.x264-GRP"}
    for name in ("Episode 01 - Pilot.srt", "subtitle.srt", ""):
        score, reason = matcher.rate({"release": name, "hash_match": True},
                                     target)
        assert (score, reason) == (100, "hash"), name


def test_a_film_hash_match_is_unaffected():
    from pinky.subs import matcher

    target = {"type": "movie", "release": "Fight.Club.1999.1080p.BluRay-GRP"}
    assert matcher.rate({"release": "anything at all", "hash_match": True},
                        target) == (100, "hash")


# --------------------------------------------------------------------------
# an episode subtitle has only two facts to offer, and both must count
# --------------------------------------------------------------------------


def test_naming_the_right_episode_reaches_the_threshold():
    """A subtitle for the right series and the right episode is not a guess.

    An anime episode's picker drew every row at 52% - 40 for the title and 12
    for the episode - because that is all the arithmetic there is: a subtitle
    called "Attack on Titan - S01E12 - Wound" carries no group, source,
    resolution or codec to agree with "[Leopard-Raws] Shingeki no Kyojin -
    S01E12". Measured against a hash reference such candidates fit 0.78 after
    re-timing, and against a second upload of the same episode they agree 0.75.
    """
    from pinky.subs import matcher

    target = matcher.target_from({
        "type": "episode", "title": "Attack on Titan", "show_title": "Attack on Titan",
        "season": 1, "episode": 12, "ids": {}})
    score, _reason = matcher.rate(
        {"release": "Attack on Titan - S01E12 - Wound", "language": "en"}, target)
    assert score >= 70, "the right episode of the right show is not 52%%: %d" % score


def test_the_wrong_episode_is_still_zero():
    """Raising the reward must not soften the penalty."""
    from pinky.subs import matcher

    target = matcher.target_from({
        "type": "episode", "title": "Attack on Titan", "show_title": "Attack on Titan",
        "season": 1, "episode": 12, "ids": {}})
    score, reason = matcher.rate(
        {"release": "Attack on Titan - S01E19 - Wound", "language": "en"}, target)
    assert score == 0 and "episode" in reason


def test_a_film_is_not_affected():
    """Films have no episode to name, so nothing here moves them."""
    from pinky.subs import matcher

    target = matcher.target_from({"type": "movie", "title": "Fight Club",
                                  "year": 1999, "ids": {}})
    score, _reason = matcher.rate(
        {"release": "Fight Club 1999", "language": "en"}, target)
    assert score < 70, "a title alone is still a title alone: %d" % score


# --------------------------------------------------------------------------
# a candidate that is for something else entirely
# --------------------------------------------------------------------------

ODYSSEY = {"type": "movie", "title": "The Odyssey", "year": 2026}
ODYSSEY_SOURCE = {"release": "The.Odyssey.2026.1080p.WEBRip.x265-INFINITY.mp4",
                  "quality": "1080p"}


@pytest.mark.parametrize("name,wrong", [
    # Measured, both of them, on one playback of The Odyssey (2026): the best
    # Hebrew subtitle in the list and the source the translation was made
    # from. Both scored 70% and read "Hebrew subtitle, 70% fit".
    ("Doctor.Odyssey.S01E18.The.Wave.Part.2.1080p.AMZN.WEB-DL.DDP", True),
    ("The.Martian.2015.720p.WEB-DL.XviD.AC3-RARBG", True),
    ("The.Odyssey.2026.1080p.WEB-DL.x264-OTHER", False),
    # A translated title shares no word with the English one, so the year is
    # what has to save it.
    ("A Odisseia 2026 1080p WEBRip", False),
    # And a name that states no year states nothing to disagree with.
    ("The Odyssey 1080p WEBRip", False),
])
def test_a_subtitle_for_a_different_title_scores_nothing(name, wrong):
    """WEIGHT_TITLE is granted to every candidate on the grounds that it came
    back from a search for this title, and the providers do not honour that.
    The film then played in English, because the Hebrew row it promised was
    thrown out at playback for ending an hour before the film does."""
    from pinky.subs import matcher

    target = matcher.target_from(ODYSSEY, ODYSSEY_SOURCE)
    score, reason = matcher.rate({"release": name}, target)

    assert (score == 0) is wrong, reason
    if wrong:
        assert reason == "wrong title"


def test_a_film_has_no_episodes():
    """A candidate naming S01E18 against a film needs no second opinion."""
    from pinky.subs import matcher

    target = matcher.target_from(ODYSSEY, ODYSSEY_SOURCE)
    assert matcher.rate({"release": "Some.Show.S01E18.1080p.WEB"}, target)[0] == 0


def test_an_episode_target_is_left_to_the_episode_check():
    """The season and episode rules are their own thing and better at it."""
    from pinky.subs import matcher

    target = matcher.target_from(
        {"type": "episode", "title": "Silo", "year": 2023, "season": 1, "episode": 1},
        {"release": "Silo.S01E01.1080p.WEB-DL-NTb", "quality": "1080p"})
    assert matcher.rate({"release": "Silo.S01E01.1080p.WEB-DL-GRP"}, target)[0] > 0


# --------------------------------------------------------------------------
# the anime scorer: what an anime subtitle actually states
# --------------------------------------------------------------------------


def _anime(release_name, episode=55, absolute=55):
    meta = {"type": "episode", "title": "Naruto Shippuden", "season": 3,
            "episode": episode, "absolute": absolute, "extra": {"anime": True}}
    return matcher.target_from(meta, {"file_name": release_name})


ANIMERG = "[AnimeRG] Naruto Shippuden - 055 [1080p] [x265] [pseudo].mkv"


def test_an_anime_subtitle_for_the_right_episode_is_seventy():
    score, reason = matcher.rate(candidate("Naruto Shippuuden - EP055"),
                                 _anime(ANIMERG))
    assert score == 70


def test_the_fansub_group_counts_with_or_without_its_brackets():
    """OpenSubtitles files "[AnimeRG] ..." as "AnimeRG. ..." and the parser
    then reads the group as "pseudo" from the tail."""
    stripped = candidate("AnimeRG. Naruto Shippuden - 055 .720p. .x265. .pseudo.srt")
    bracketed = candidate("[AnimeRG] Naruto Shippuden - 055 [720p].ass")
    for entry in (stripped, bracketed):
        score, reason = matcher.rate(entry, _anime(ANIMERG))
        assert score >= 90 and "group" in reason, entry["release"]


def test_a_track_extracted_from_the_same_mkv_is_identical():
    """"..._track3_[eng]" is the English track of that exact file."""
    entry = candidate("[AnimeRG] Naruto Shippuden - 055 [1080p] [x265] "
                      "[pseudo]_track3_[eng]")
    assert matcher.rate(entry, _anime(ANIMERG)) == (100, "identical release name")


def test_a_bare_episode_number_is_the_episode_and_a_wrong_one_is_zero():
    """"055_LEG" and "155_LEG" were both 45: a name that is only a number
    stated nothing to the parser, so the wrong episode tied the right one."""
    assert matcher.rate(candidate("055_LEG"), _anime(ANIMERG))[0] == 70
    assert matcher.rate(candidate("155_LEG"), _anime(ANIMERG)) == (0, "wrong episode")


def test_a_number_that_is_a_resolution_or_a_title_is_not_an_episode():
    target = _anime(ANIMERG, episode=1, absolute=1)
    assert matcher.rate(candidate("1080p.ass"), target)[0] > 0
    assert matcher.rate(candidate("86 - Eighty Six - 01"), target)[0] > 0


def test_the_same_cut_counts_for_anime():
    """A Blu-ray and a broadcast are different cuts of an anime episode."""
    bd = "[uP] Naruto Shippuden - 055 (BDRip 1080p x264 AAC Multi).mkv"
    same = matcher.rate(candidate("Naruto Shippuden - 055 [BD 1080p].ass"), _anime(bd))
    other = matcher.rate(candidate("Naruto Shippuden - 055 [TV 1080p].ass"), _anime(bd))
    assert same[0] > other[0] and "cut" in same[1]


def test_films_and_series_never_reach_the_anime_scorer(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("the anime scorer ran for a film or a series")

    monkeypatch.setattr(matcher, "_rate_anime", explode)
    matcher.rate(candidate("Dune.Part.Two.2024.1080p.WEB-DL-FLUX"), TARGET)
    series = matcher.target_from({"type": "episode", "title": "Silo",
                                  "season": 1, "episode": 1,
                                  "extra": {"anime": False}},
                                 {"file_name": "Silo.S01E01.1080p.WEB.h264-GRP"})
    matcher.rate(candidate("Silo.S01E01.1080p.WEB.h264-GRP"), series)


def test_a_double_episode_subtitle_fits_a_double_episode_release():
    """ALF 3x05 is released on its own and as "S03E04+E05": the subtitle
    typed for two episodes is an episode out on the first."""
    from pinky.subs import matcher
    meta = {"type": "episode", "title": "ALF", "season": 3, "episode": 5, "year": 1986}
    subtitle = {"release": "ALF.S03E04-05.DVDRip.XviD-MEMETiC"}

    def fit(release_name):
        return matcher.rate(subtitle, matcher.target_from(meta, {"file_name": release_name}))

    assert fit("ALF S03E04+E05 Tonight, Tonight.mp4")[0] >= 70
    assert fit("ALF.S03E05.Tonight.Tonight.2.1080p.WEB-DL.AAC2.0.H.264-DAWN.mkv") == (
        40, "double episode")
    meta["episode"] = 6
    assert fit("ALF.S03E06.1080p.WEB-DL.mkv")[0] == 0


@pytest.mark.parametrize("title, year, playing, subtitle, wrong", [
    # Wizdom files Dune: Part Two under Dune, and it scored 99.
    ("Dune", 2021, "Dune.2021.2160p.WEB-DL.DDP5.1.Atmos.HEVC-FLUX.mkv",
     "Dune.Part.Two.2024.2160p.WEB-DL.DDP5.1.Atmos.HEVC-FLUX", True),
    ("Dune: Part Two", 2024, "Dune.Part.Two.2024.1080p.WEB.mkv",
     "Dune.2021.1080p.WEB.H264-GRP", True),
    ("Alien", 1979, "Alien.1979.1080p.BluRay.x264-GRP.mkv",
     "Aliens.1986.SE.720p.HDTV.DTS.x264-DON", True),
    # The film this one remade: a re-release is never dated before the film.
    ("Dune", 2021, "Dune.2021.1080p.BluRay.x264-GRP.mkv",
     "Dune.1984.1080p.BluRay.x264-GRP", True),
    # The same film, dated by its re-release.
    ("Blade Runner", 1982, "Blade.Runner.1982.The.Final.Cut.1080p.BluRay.mkv",
     "Blade.Runner.The.Final.Cut.2007.25fps.592.728kbps.V5.WunSeeDee", False),
    ("Alien", 1979, "Alien.1979.1080p.BluRay.x264-GRP.mkv",
     "Alien.Directors.Cut.2003.DVDRIP.XViD-DigitalVX", False),
    ("The Shawshank Redemption", 1994, "The.Shawshank.Redemption.1994.1080p.BluRay.mkv",
     "The.Shawshank.Redemption.2000.DVDRip.Xvid.AC3.iNTERNAL-FFM", False),
    # A year that is the film's own name is not a date.
    ("1917", 2019, "1917.2019.1080p.BluRay.mkv", "1917.1080p.BluRay.x264-SPARKS", False),
    ("Blade Runner 2049", 2017, "Blade.Runner.2049.2017.1080p.BluRay.mkv",
     "Blade.Runner.2049.1080p.BluRay.x264-SPARKS", False),
    ("Dune", 2021, "Dune.2021.1080p.BluRay.x264-GRP.mkv",
     "Dune.Part.One.2021.1080p.BluRay.x264-CEBRAY", False),
])
def test_a_sequel_or_a_remake_is_another_film(title, year, playing, subtitle, wrong):
    from pinky.subs import matcher
    meta = {"type": "movie", "title": title, "year": year}
    score, reason = matcher.rate(
        {"release": subtitle}, matcher.target_from(meta, {"file_name": playing}))
    assert (score == 0 and reason == "wrong title") is wrong


def test_a_sequel_is_told_apart_by_the_latin_title_on_a_hebrew_interface():
    """`title` is Hebrew there, and a subtitle's name never is."""
    from pinky.subs import matcher
    playing = "Dune.2021.2160p.WEB-DL.DDP5.1.Atmos.HEVC-FLUX.mkv"
    sequel = {"release": "Dune.Part.Two.2024.2160p.WEB-DL.DDP5.1.Atmos.HEVC-FLUX"}
    hebrew = {"type": "movie", "title": u"\u05d7\u05d5\u05dc\u05d9\u05ea", "year": 2021}
    known = dict(hebrew, original_title="Dune")
    assert matcher.rate(sequel, matcher.target_from(known, {"file_name": playing}))[0] == 0
    # With no Latin name to compare against, nothing is concluded.
    assert matcher.rate(sequel, matcher.target_from(hebrew, {"file_name": playing}))[0] > 0
