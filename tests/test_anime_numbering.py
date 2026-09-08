# -*- coding: utf-8 -*-
"""Anime is named and numbered differently, and every layer had it wrong.

Found by surveying a thousand titles rather than by imagining cases. Three
separate mistakes, each of which alone is enough to make an anime episode
unplayable:

* the two providers that search **by name** were handed `original_title`,
  which for anime is Japanese in Japanese script. Measured against Nyaa, the
  Japanese title returns zero results for Doraemon, Reborn, Frieren and
  Madoka alike, while the English name returns 75, 45, 6 and 0.
* the episode number passed was the **season-relative** one. Fansub groups
  number absolutely, so season 8 episode 14 of Reborn is released as episode
  203 - and searching for 14 came back with forty-five results for episode
  *149*, because Nyaa matches "14" inside "149".
* nyaa then **did not check** what it got back, so those forty-five wrong
  episodes went into the picker looking like answers.
"""
import pytest

from katan.utils import release


# --------------------------------------------------------------------------
# reading the number off a release name
# --------------------------------------------------------------------------


def numbers(name):
    parsed = release.parse(name)
    return parsed["season"], parsed["episode"], parsed["absolute"]


def test_a_bare_number_before_a_quality_token_is_an_episode():
    """The tidy "Show - 12" convention is not the only one in use."""
    assert numbers("[KSN]Katekyo Hitman Reborn! 149 [576p][H264]")[2] == 149


def test_a_version_suffix_does_not_hide_the_number():
    assert numbers("[Late] Bleach TYBW 46 v2 (Web, x264, EAC3)")[2] == 46


def test_underscores_are_separators_like_any_other():
    assert numbers("[Yonkou]_One_Piece_539_[HD][01891224].mkv")[2] == 539


def test_the_word_episode_is_taken_at_its_word():
    assert numbers("[Ommex] Doraemon (2005) Episode 930 [ENG SUB][1080p]")[2] \
        == 930


@pytest.mark.parametrize("name", [
    "The.Matrix.1999.1080p.BluRay.x264-AMIABLE",
    "Dune.Part.Two.2024.1080p.WEB-DL.H264-GRP",
    "Blade.Runner.2049.2017.2160p.UHD.BluRay.x265-GRP",
    "12.Angry.Men.1957.1080p.BluRay.x264-AMIABLE",
])
def test_a_year_is_never_an_episode_number(name):
    """A year is the one number that reliably sits exactly where an episode
    number sits - "the matrix 1999 1080p" - so it is refused outright. No
    anime has run for nineteen hundred episodes."""
    assert numbers(name)[2] == 0


def test_a_films_own_number_is_not_an_episode():
    assert numbers("12.Angry.Men.1957.1080p.BluRay.x264-AMIABLE")[2] == 0


# --------------------------------------------------------------------------
# batches
# --------------------------------------------------------------------------


def test_a_batch_says_which_episodes_it_holds():
    parsed = release.parse(
        "[HorribleSubs] Kateikyoushi Hitman Reborn! (125-203) [720p] (Batch)")
    assert parsed["episode_range"] == (125, 203)


def test_a_batch_answers_for_an_episode_inside_it():
    """For a long-running anime the batch is often the only thing seeded."""
    parsed = release.parse("[SG]_Hitman_Reborn_[132-203]")
    assert release.matches_episode(parsed, 8, 14, 203)


def test_a_batch_does_not_answer_for_an_episode_outside_it():
    parsed = release.parse("[SG]_Hitman_Reborn_[132-203]")
    assert not release.matches_episode(parsed, 1, 5, 5)


def test_a_span_of_years_is_not_a_span_of_episodes():
    assert release.parse("Some.Documentary.1990-2000.1080p")["episode_range"] \
        is None


# --------------------------------------------------------------------------
# absolute against season-relative
# --------------------------------------------------------------------------


def test_an_absolute_release_is_judged_against_the_absolute_number():
    parsed = release.parse("[Group] Reborn! - 203 [1080p]")
    assert release.matches_episode(parsed, 8, 14, 203)
    assert not release.matches_episode(parsed, 8, 14, 14)


def test_the_season_relative_number_is_the_default():
    """A single-season show has no distinction, and every caller that does
    not know any better must keep working."""
    parsed = release.parse("[Group] Frieren - 12 [1080p]")
    assert release.matches_episode(parsed, 1, 12)


def test_the_wrong_episode_is_still_refused():
    parsed = release.parse("[KSN]Katekyo Hitman Reborn! 149 [576p]")
    assert not release.matches_episode(parsed, 8, 14, 203)


def test_a_named_season_and_episode_still_wins():
    parsed = release.parse("[Feibanyama] BLEACH Thousand Year Blood War S01E46")
    assert release.matches_episode(parsed, 1, 46, 46)


# --------------------------------------------------------------------------
# working the absolute number out
# --------------------------------------------------------------------------


def test_the_absolute_number_counts_every_earlier_season(monkeypatch):
    from katan.meta import tmdb
    monkeypatch.setattr(tmdb, "_call", lambda path, ttl=None, **kw: {
        "seasons": [{"season_number": 0, "episode_count": 9},
                    {"season_number": 1, "episode_count": 100},
                    {"season_number": 2, "episode_count": 89},
                    {"season_number": 3, "episode_count": 14}]})
    assert tmdb.absolute_episode("1", 3, 5) == 194


def test_specials_are_not_part_of_the_count(monkeypatch):
    from katan.meta import tmdb
    monkeypatch.setattr(tmdb, "_call", lambda path, ttl=None, **kw: {
        "seasons": [{"season_number": 0, "episode_count": 50},
                    {"season_number": 1, "episode_count": 12}]})
    assert tmdb.absolute_episode("1", 2, 3) == 15


def test_a_first_season_needs_no_arithmetic(monkeypatch):
    """And must not pay for a lookup to be told so."""
    from katan.meta import tmdb

    def explode(*args, **kwargs):
        raise AssertionError("asked TMDB for a first-season episode")

    monkeypatch.setattr(tmdb, "_call", explode)
    assert tmdb.absolute_episode("1", 1, 7) == 7


# --------------------------------------------------------------------------
# what the providers are handed
# --------------------------------------------------------------------------


@pytest.fixture
def anime_meta(monkeypatch):
    from katan import play
    from katan.meta import tmdb

    monkeypatch.setattr(tmdb, "show", lambda tmdb_id: {
        "ids": {"tmdb": tmdb_id, "imdb": "tt1224144"},
        "title": u"המורה הפרטי המתנקש",
        "original_title": u"家庭教師ヒットマン REBORN!",
        "year": 2006, "art": {}, "extra": {"anime": True}})
    monkeypatch.setattr(tmdb, "episodes", lambda tmdb_id, season: [])
    monkeypatch.setattr(tmdb, "english_title", lambda kind, tmdb_id: "REBORN!")
    monkeypatch.setattr(tmdb, "absolute_episode",
                        lambda tmdb_id, season, episode: 203)
    return play.build_meta({"type": "episode", "tmdb": "45857",
                            "season": 8, "episode": 14})


def test_anime_is_searched_by_its_english_name(anime_meta):
    from katan.sources.providers import nyaa
    assert nyaa._query_for(anime_meta) == "REBORN! 203"


def test_both_anime_providers_agree_on_the_query(anime_meta):
    from katan.sources.providers import animetosho, nyaa
    assert nyaa._query_for(anime_meta) == animetosho._query_for(anime_meta)


def test_nothing_is_looked_up_for_a_film_that_is_not_anime(monkeypatch):
    """The two extra calls are for anime, and anime is most of nothing."""
    from katan import play
    from katan.meta import tmdb

    monkeypatch.setattr(tmdb, "movie", lambda tmdb_id: {
        "ids": {"tmdb": tmdb_id}, "title": "The Matrix", "year": 1999,
        "art": {}, "extra": {"anime": False}})

    def explode(*args, **kwargs):
        raise AssertionError("looked up an English title for a live-action film")

    monkeypatch.setattr(tmdb, "english_title", explode)
    meta = play.build_meta({"type": "movie", "tmdb": "603"})
    assert "search_title" not in meta


# --------------------------------------------------------------------------
# nyaa checking what it got back
# --------------------------------------------------------------------------


RSS = u"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:nyaa="https://nyaa.si/xmlns/nyaa">
<channel>%s</channel></rss>"""

ITEM = u"""<item>
  <title>%s</title>
  <nyaa:infoHash>%s</nyaa:infoHash>
  <nyaa:size>1.2 GiB</nyaa:size>
  <nyaa:seeders>20</nyaa:seeders>
</item>"""


def feed(*names):
    return RSS % "".join(ITEM % (name, "%040x" % (index + 1))
                         for index, name in enumerate(names))


def test_nyaa_refuses_the_episode_it_was_not_asked_for():
    """Asking for episode 14 returned forty-five results for episode 149,
    because Nyaa matches the number as text."""
    from katan.sources.providers import nyaa
    meta = {"type": "episode", "season": 8, "episode": 14, "absolute": 203}

    got = nyaa._parse_rss(feed(
        "[KSN]Katekyo Hitman Reborn! 149 [576p][H264]",
        "[Group] Reborn! - 203 [1080p]"), meta)

    assert [s["title"] for s in got] == ["[Group] Reborn! - 203 [1080p]"]


def test_nyaa_keeps_a_batch_that_covers_the_episode():
    from katan.sources.providers import nyaa
    meta = {"type": "episode", "season": 8, "episode": 14, "absolute": 203}
    got = nyaa._parse_rss(feed("[SG]_Hitman_Reborn_[132-203]"), meta)
    assert len(got) == 1


def test_nyaa_keeps_a_name_that_says_nothing_about_episodes():
    """Batches are routinely named "Complete Series" with the numbers only in
    the file list, and the debrid layer picks the right file out of a pack.
    An unnumbered name is an unknown, not a wrong answer."""
    from katan.sources.providers import nyaa
    meta = {"type": "episode", "season": 8, "episode": 14, "absolute": 203}
    got = nyaa._parse_rss(feed("Katekyo Hitman Reborn Complete Series"), meta)
    assert len(got) == 1


def test_nyaa_leaves_films_alone():
    from katan.sources.providers import nyaa
    got = nyaa._parse_rss(feed("Some Anime Movie 1080p"), {"type": "movie"})
    assert len(got) == 1


# --------------------------------------------------------------------------
# picking the right file out of a pack
# --------------------------------------------------------------------------


def test_the_right_file_is_picked_out_of_a_fansub_batch():
    """The batch is often the only thing seeded, and its files are numbered
    absolutely - so without the absolute number the right file is sitting in
    the pack and nothing matches it, and the episode refuses to play."""
    from katan.debrid import base

    files = [{"name": "Reborn! - 202.mkv", "size": 400 * 1024 ** 2},
             {"name": "Reborn! - 203.mkv", "size": 400 * 1024 ** 2},
             {"name": "Reborn! - 204.mkv", "size": 400 * 1024 ** 2}]
    chosen = base.DebridService().pick_file(
        files, {}, {"type": "episode", "season": 8,
                    "episode": 14, "absolute": 203})
    assert chosen["name"] == "Reborn! - 203.mkv"


def test_a_pack_without_the_episode_still_refuses():
    from katan.debrid import base
    files = [{"name": "Reborn! - 100.mkv", "size": 400 * 1024 ** 2},
             {"name": "Reborn! - 101.mkv", "size": 400 * 1024 ** 2}]
    assert base.DebridService().pick_file(
        files, {}, {"type": "episode", "season": 8,
                    "episode": 14, "absolute": 203}) is None


def test_a_season_marker_makes_the_number_season_relative():
    """"[AnimeRG] Shingeki no Kyojin S3 - 11" is season three episode eleven,
    not absolute episode eleven. Reading it the other way refused a correct
    source, which is worse than the loose matching the filter exists to
    prevent."""
    parsed = release.parse(
        "[AnimeRG] Shingeki no Kyojin S3 - 11 (Attack on Titan - 48) [1080p]")
    assert release.matches_episode(parsed, 3, 11, 48)


def test_the_wrong_episode_of_the_right_season_is_still_refused():
    parsed = release.parse("[KaiDubs] Attack on Titan S3 - 10 [720p]")
    assert not release.matches_episode(parsed, 3, 11, 48)


def test_an_absolute_number_with_no_season_named_stays_absolute():
    parsed = release.parse("[Rip Time] Attack on Titan Final Season - 62.mkv")
    assert not release.matches_episode(parsed, 3, 11, 48)
    assert release.matches_episode(parsed, 3, 11, 62)


def test_a_season_pack_still_answers_for_any_episode_in_it():
    parsed = release.parse("Show.S02.COMPLETE.1080p.WEB-DL-GRP")
    assert release.matches_episode(parsed, 2, 5, 5)
    assert not release.matches_episode(parsed, 3, 5, 5)


# --------------------------------------------------------------------------
# the fourth way anime numbering breaks: the address itself
#
# TMDB folds a whole multi-year arc into one season - Bleach's Thousand-Year
# Blood War is "season 2", numbered 1 to 50 - while Kitsu, AniDB and every
# release group treat each cour as its own series numbered from 1. So the
# address every id-keyed provider is given, imdb:2:46, is one nobody indexes.
# Measured against Torrentio: tt0434665:2:46 returns nothing at all, and
# kitsu:49444:6 - the same episode - returns nine sources.
# --------------------------------------------------------------------------

# Exactly what kitsu.io returned for "Bleach Thousand-Year Blood War".
BLEACH_COURS = {"data": [
    {"id": "43078", "attributes": {"canonicalTitle": "BLEACH: Sennen Kessen-hen",
                                   "episodeCount": 13, "startDate": "2022-10-10",
                                   "subtype": "TV"}},
    {"id": "46903", "attributes": {"canonicalTitle": "BLEACH: Sennen Kessen-hen - Ketsubetsu-tan",
                                   "episodeCount": 13, "startDate": "2023-07-08",
                                   "subtype": "TV"}},
    {"id": "48015", "attributes": {"canonicalTitle": "BLEACH: Sennen Kessen-hen - Soukoku-tan",
                                   "episodeCount": 14, "startDate": "2024-10-05",
                                   "subtype": "TV"}},
    {"id": "49444", "attributes": {"canonicalTitle": "BLEACH: Sennen Kessen-hen - Kashin-tan",
                                   "episodeCount": 10, "startDate": "2026-07-25",
                                   "subtype": "TV"}},
    # The two that come back in the same search and must not be walked over:
    # a one-episode recap sitting between the second and third cours, and a
    # theme song. Counting either shifts every episode after it by one.
    {"id": "48914", "attributes": {"canonicalTitle": "BLEACH: Sennen Kessen-hen - Recap",
                                   "episodeCount": 1, "startDate": "2023-09-02",
                                   "subtype": "special"}},
    {"id": "45543", "attributes": {"canonicalTitle": "Rapport", "episodeCount": 1,
                                   "startDate": "2021-11-23", "subtype": "music"}},
]}


@pytest.fixture
def bleach_cours(monkeypatch):
    from katan.meta import kitsu
    monkeypatch.setattr(kitsu, "_get", lambda path, params=None, **kw: BLEACH_COURS)
    return kitsu


@pytest.mark.parametrize("episode,expected", [
    (1, ("43078", 1)),
    (13, ("43078", 13)),
    (14, ("46903", 1)),          # straight over the cour boundary
    (26, ("46903", 13)),
    (27, ("48015", 1)),          # and over the recap, which is not a cour
    (40, ("48015", 14)),
    (41, ("49444", 1)),
    (46, ("49444", 6)),          # the one that started this
    (50, ("49444", 10)),
])
def test_a_season_number_becomes_a_cour_and_a_number(episode, expected,
                                                     bleach_cours):
    assert bleach_cours.episode_address(
        "Bleach", episode, "Thousand-Year Blood War", 50) == expected


def test_it_gives_up_rather_than_guess_when_the_counts_disagree(bleach_cours):
    """A wrong address is worse than none.

    It plays a real episode that is not the one asked for, and nothing
    downstream can catch that - the file is exactly what it says it is. So
    the cours have to add up to the season TMDB describes, or no address is
    offered at all.
    """
    assert bleach_cours.episode_address("Bleach", 46,
                                        "Thousand-Year Blood War", 99) is None


def test_an_unnamed_season_is_not_guessed_at(bleach_cours):
    """The arc's name is the only thing separating it from its parent series.

    Without one, a text search for "Bleach" returns the 366-episode original,
    six one-episode specials and several unrelated shows - measured - and
    episode 46 lands in the wrong one of them.
    """
    assert bleach_cours.episode_address("Bleach", 46, "", 50) is None
    assert bleach_cours.episode_address("", 46, "Thousand-Year Blood War", 50) is None


def test_an_episode_past_the_end_is_not_forced_into_the_last_cour(bleach_cours):
    assert bleach_cours.episode_address("Bleach", 51,
                                        "Thousand-Year Blood War", 50) is None
