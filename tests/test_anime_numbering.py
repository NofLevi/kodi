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

from pinky.utils import release


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


def test_a_bare_number_before_a_pixel_dimension_is_the_episode():
    """`[Tsuki]_One_Piece_638_[848x480]` carried no episode number at all
    under the old list, which only recognised the "480p" shorthand - so
    episode 638 was offered for a request asking for episode 40 as freely as
    the real thing, and nothing downstream ever knew to refuse it. Some
    fansub groups write the raw dimensions instead."""
    parsed = release.parse("[Tsuki]_One_Piece_638_[848x480][A40D75D0].avi")
    assert parsed["absolute"] == 638
    assert not release.matches_episode(parsed, 1, 40, 40)

    wanted = release.parse("[Tsuki]_One_Piece_040_[848x480][B1234567].avi")
    assert wanted["absolute"] == 40
    assert release.matches_episode(wanted, 1, 40, 40)


def test_season_one_counted_to_the_end_is_the_absolute_number():
    """Netflix and Jimaku file Hikaru no Go's TMDB 3x06 as S01E66."""
    parsed = release.parse("ヒカルの碁.S01E66.WEBRip.Netflix.ja[cc]")
    assert release.matches_episode(parsed, 3, 6, 66)
    assert not release.matches_episode(parsed, 3, 6, 67)
    assert not release.matches_episode(parsed, 3, 6), "only when the absolute number is known"


def test_a_stated_season_has_to_agree_with_a_bare_number():
    """Measured on Jimaku: "S3 - 06" was offered for season one's episode six."""
    parsed = release.parse("[NanakoRaws] Oshi no Ko S3 - 06 (AT-X 1080p HEVC AAC)")
    assert not release.matches_episode(parsed, 1, 6, 6)
    assert release.matches_episode(parsed, 3, 6, 28)
    assert not release.matches_episode(release.parse("[Grp] Show S1 - 11"), 3, 11, 35)


def test_anime_titles_puts_romaji_first_and_keeps_every_latin_name(monkeypatch):
    """Romaji first, because the subtitle search asks under the first two.
    Then every other Latin-script name: Digimon's release name, "Digimon
    Adventure", is tagged US and IT, not JP romaji, and requiring JP romaji
    hid 26 of its 34 releases. Native script and initialisms stay out."""
    from pinky.meta import tmdb
    monkeypatch.setattr(tmdb, "_call", lambda path, ttl=None, **kw: {
        "results": [
            {"iso_3166_1": "JP", "title": "進撃の巨人", "type": "title"},
            {"iso_3166_1": "US", "title": "Attack on Titan: Shingeki no Kyojin",
             "type": "english and japanese title"},
            {"iso_3166_1": "JP", "title": "Shingeki no Kyojin", "type": "romaji"},
            {"iso_3166_1": "JP", "title": "SNK", "type": "initialism"},
            {"iso_3166_1": "BR", "title": "Ataque dos Titãs", "type": "title"},
        ]})
    assert tmdb.anime_titles("show", "1429") == [
        "Shingeki no Kyojin", "Attack on Titan: Shingeki no Kyojin",
        "Ataque dos Titãs"]


def test_anime_titles_leaves_out_what_tmdb_calls_another_part(monkeypatch):
    """"Digimon Adventure 02" is TMDB's "alt series 2 title" - the sequel.
    Knowing it would let the sequel's releases through as this show."""
    from pinky.meta import tmdb
    monkeypatch.setattr(tmdb, "_call", lambda path, ttl=None, **kw: {
        "results": [
            {"iso_3166_1": "US", "title": "Digimon Adventure",
             "type": "title used on streaming services"},
            {"iso_3166_1": "US", "title": "Digimon Adventure 02",
             "type": "alt series 2 title"},
            {"iso_3166_1": "US", "title": "Digimon Adventure Zero Two",
             "type": "series 2 title"},
        ]})
    assert tmdb.anime_titles("show", "31654") == ["Digimon Adventure"]


def test_anime_titles_keeps_every_romaji_arc_name(monkeypatch):
    """An arc is a season of the same TMDB show: "Kimetsu no Yaiba: Hashira
    Geiko-hen" is Demon Slayer season 4, and dropping it hid that season's
    own releases - which the engine gate caught before this shipped."""
    from pinky.meta import tmdb
    monkeypatch.setattr(tmdb, "_call", lambda path, ttl=None, **kw: {
        "results": [
            {"iso_3166_1": "JP", "title": "Kimetsu no Yaiba", "type": "romaji"},
            {"iso_3166_1": "JP", "title": "Kimetsu no Yaiba: Hashira Geiko-hen",
             "type": "season 4 romaji"},
        ]})
    assert tmdb.anime_titles("show", "85937") == [
        "Kimetsu no Yaiba", "Kimetsu no Yaiba: Hashira Geiko-hen"]


def test_three_by_fifty_five_is_fifty_five_when_tmdb_already_counts(monkeypatch):
    """TMDB numbers Naruto Shippuden absolutely inside each season - season
    3 is episodes 54 to 71 - so adding the earlier seasons made 3x55 into
    108, and the picker offered episode 108's subtitle at 71% for 55."""
    from pinky.meta import tmdb

    def call(path, ttl=None, **kw):
        if "/season/" in path:
            return {"episodes": [{"episode_number": n} for n in range(54, 72)]}
        return {"seasons": [{"season_number": 1, "episode_count": 32},
                            {"season_number": 2, "episode_count": 21},
                            {"season_number": 3, "episode_count": 18}]}

    monkeypatch.setattr(tmdb, "_call", call)
    assert tmdb.absolute_episode("31910", 3, 55) == 55


def test_anime_titles_reads_the_movie_shaped_response_too(monkeypatch):
    """A movie's alternative_titles keys the list "titles", not "results"."""
    from pinky.meta import tmdb
    monkeypatch.setattr(tmdb, "_call", lambda path, ttl=None, **kw: {
        "titles": [{"iso_3166_1": "JP", "title": "Kimi no Na wa.",
                   "type": "romaji"}]})
    assert tmdb.anime_titles("movie", "372058") == ["Kimi no Na wa."]


def test_anime_titles_costs_nothing_with_no_tmdb_id(monkeypatch):
    from pinky.meta import tmdb

    def explode(*args, **kwargs):
        raise AssertionError("asked TMDB with no id")

    monkeypatch.setattr(tmdb, "_call", explode)
    assert tmdb.anime_titles("show", "") == []


# --------------------------------------------------------------------------
# working the absolute number out
# --------------------------------------------------------------------------


def test_the_absolute_number_counts_every_earlier_season(monkeypatch):
    from pinky.meta import tmdb
    monkeypatch.setattr(tmdb, "_call", lambda path, ttl=None, **kw: {
        "seasons": [{"season_number": 0, "episode_count": 9},
                    {"season_number": 1, "episode_count": 100},
                    {"season_number": 2, "episode_count": 89},
                    {"season_number": 3, "episode_count": 14}]})
    assert tmdb.absolute_episode("1", 3, 5) == 194


def test_specials_are_not_part_of_the_count(monkeypatch):
    from pinky.meta import tmdb
    monkeypatch.setattr(tmdb, "_call", lambda path, ttl=None, **kw: {
        "seasons": [{"season_number": 0, "episode_count": 50},
                    {"season_number": 1, "episode_count": 12}]})
    assert tmdb.absolute_episode("1", 2, 3) == 15


def test_a_first_season_needs_no_arithmetic(monkeypatch):
    """And must not pay for a lookup to be told so."""
    from pinky.meta import tmdb

    def explode(*args, **kwargs):
        raise AssertionError("asked TMDB for a first-season episode")

    monkeypatch.setattr(tmdb, "_call", explode)
    assert tmdb.absolute_episode("1", 1, 7) == 7


# --------------------------------------------------------------------------
# what the providers are handed
# --------------------------------------------------------------------------


@pytest.fixture
def anime_meta(monkeypatch):
    from pinky import play
    from pinky.meta import tmdb

    monkeypatch.setattr(tmdb, "show", lambda tmdb_id: {
        "ids": {"tmdb": tmdb_id, "imdb": "tt1224144"},
        "title": u"המורה הפרטי המתנקש",
        "original_title": u"家庭教師ヒットマン REBORN!",
        "year": 2006, "art": {}, "extra": {"anime": True}})
    monkeypatch.setattr(tmdb, "episodes", lambda tmdb_id, season: [])
    monkeypatch.setattr(tmdb, "english_title", lambda kind, tmdb_id: "REBORN!")
    monkeypatch.setattr(tmdb, "absolute_episode",
                        lambda tmdb_id, season, episode: 203)
    monkeypatch.setattr(tmdb, "anime_titles",
                        lambda kind, tmdb_id: ["Katekyo Hitman Reborn!"])
    return play.build_meta({"type": "episode", "tmdb": "45857",
                            "season": 8, "episode": 14})


def test_anime_is_searched_by_its_english_name(anime_meta):
    from pinky.sources.providers import nyaa
    assert nyaa._query_for(anime_meta) == "REBORN! 203"


def test_both_anime_providers_agree_on_the_query(anime_meta):
    from pinky.sources.providers import animetosho, nyaa
    assert nyaa._query_for(anime_meta) == animetosho._query_for(anime_meta)


def test_anime_meta_carries_its_romaji_alias(anime_meta):
    """`scoring._a_different_series` reads exactly this field - populating
    it here is what stops a release titled after the Japanese name being
    scored as a different show sharing the name."""
    assert anime_meta["aliases"] == ["Katekyo Hitman Reborn!"]


def test_a_romaji_named_release_is_not_a_different_series(anime_meta):
    from pinky.sources import scoring
    source = {"title": "[SubsPlease] Katekyo Hitman Reborn! - 203 [1080p].mkv"}
    assert scoring._a_different_series(source, anime_meta) == ""


def test_nothing_is_looked_up_for_a_film_that_is_not_anime(monkeypatch):
    """The two extra calls are for anime, and anime is most of nothing."""
    from pinky import play
    from pinky.meta import tmdb

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
    from pinky.sources.providers import nyaa
    meta = {"type": "episode", "season": 8, "episode": 14, "absolute": 203}

    got = nyaa._parse_rss(feed(
        "[KSN]Katekyo Hitman Reborn! 149 [576p][H264]",
        "[Group] Reborn! - 203 [1080p]"), meta)

    assert [s["title"] for s in got] == ["[Group] Reborn! - 203 [1080p]"]


def test_nyaa_keeps_a_batch_that_covers_the_episode():
    from pinky.sources.providers import nyaa
    meta = {"type": "episode", "season": 8, "episode": 14, "absolute": 203}
    got = nyaa._parse_rss(feed("[SG]_Hitman_Reborn_[132-203]"), meta)
    assert len(got) == 1


def test_nyaa_keeps_a_name_that_says_nothing_about_episodes():
    """Batches are routinely named "Complete Series" with the numbers only in
    the file list, and the debrid layer picks the right file out of a pack.
    An unnumbered name is an unknown, not a wrong answer."""
    from pinky.sources.providers import nyaa
    meta = {"type": "episode", "season": 8, "episode": 14, "absolute": 203}
    got = nyaa._parse_rss(feed("Katekyo Hitman Reborn Complete Series"), meta)
    assert len(got) == 1


def test_nyaa_leaves_films_alone():
    from pinky.sources.providers import nyaa
    got = nyaa._parse_rss(feed("Some Anime Movie 1080p"), {"type": "movie"})
    assert len(got) == 1


# --------------------------------------------------------------------------
# picking the right file out of a pack
# --------------------------------------------------------------------------


def test_the_right_file_is_picked_out_of_a_fansub_batch():
    """The batch is often the only thing seeded, and its files are numbered
    absolutely - so without the absolute number the right file is sitting in
    the pack and nothing matches it, and the episode refuses to play."""
    from pinky.debrid import base

    files = [{"name": "Reborn! - 202.mkv", "size": 400 * 1024 ** 2},
             {"name": "Reborn! - 203.mkv", "size": 400 * 1024 ** 2},
             {"name": "Reborn! - 204.mkv", "size": 400 * 1024 ** 2}]
    chosen = base.DebridService().pick_file(
        files, {}, {"type": "episode", "season": 8,
                    "episode": 14, "absolute": 203})
    assert chosen["name"] == "Reborn! - 203.mkv"


def test_a_pack_without_the_episode_still_refuses():
    from pinky.debrid import base
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
    from pinky.meta import kitsu
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


# --------------------------------------------------------------------------
# the ordinary shape: one TMDB season is one broadcast run
#
# The arc walk above needs the season to have a name, and most anime seasons
# have none - TMDB calls them "Season 1", "Season 2". Measured across 166
# anime episodes, that reached a fifth of them. Where the seasons line up
# one-for-one with Kitsu's entries the season number is the whole address.
# --------------------------------------------------------------------------

# What kitsu.io returns for "KonoSuba": three broadcast runs, two OVAs, a
# film, and a spin-off whose season is the same shape as the real ones.
KONOSUBA = {"data": [
    {"id": "10941", "attributes": {
        "canonicalTitle": "Kono Subarashii Sekai ni Shukufuku wo!",
        "titles": {"en_jp": "Kono Subarashii Sekai ni Shukufuku wo!"},
        "episodeCount": 10, "startDate": "2016-01-14", "subtype": "TV"}},
    {"id": "11937", "attributes": {
        "canonicalTitle": "Kono Subarashii Sekai ni Shukufuku wo! 2",
        "titles": {"en_jp": "Kono Subarashii Sekai ni Shukufuku wo! 2"},
        "episodeCount": 10, "startDate": "2017-01-12", "subtype": "TV"}},
    {"id": "44911", "attributes": {
        "canonicalTitle": "Kono Subarashii Sekai ni Shukufuku wo! 3",
        "titles": {"en_jp": "Kono Subarashii Sekai ni Shukufuku wo! 3"},
        "episodeCount": 11, "startDate": "2024-04-10", "subtype": "TV"}},
    # The spin-off. Same franchise, same length of season, different show -
    # and only its name says so.
    {"id": "46139", "attributes": {
        "canonicalTitle": "Kono Subarashii Sekai ni Bakuen wo!",
        "titles": {"en_jp": "Kono Subarashii Sekai ni Bakuen wo!"},
        "episodeCount": 12, "startDate": "2023-04-05", "subtype": "TV"}},
    {"id": "11752", "attributes": {
        "canonicalTitle": "Kono Subarashii Sekai ni Shukufuku wo! OVA",
        "titles": {}, "episodeCount": 1,
        "startDate": "2016-06-24", "subtype": "OVA"}},
    {"id": "41440", "attributes": {
        "canonicalTitle": "Kono Subarashii Sekai ni Shukufuku wo! Movie",
        "titles": {}, "episodeCount": 1,
        "startDate": "2019-08-30", "subtype": "movie"}},
]}

KONOSUBA_NAMES = ["Kono Subarashii Sekai ni Shukufuku wo!", ""]


@pytest.fixture
def konosuba(monkeypatch):
    from pinky.meta import kitsu
    monkeypatch.setattr(kitsu, "_get", lambda path, params=None, **kw: KONOSUBA)
    return kitsu


@pytest.mark.parametrize("season,episode,expected", [
    (1, 1, ("10941", 1)),
    (2, 10, ("11937", 10)),
    (3, 5, ("44911", 5)),
])
def test_a_season_number_is_the_address(season, episode, expected, konosuba):
    assert konosuba.season_address(KONOSUBA_NAMES, season, episode,
                                   [10, 10, 11]) == expected


def test_the_spin_off_is_not_counted_as_a_season(konosuba):
    """It is the same franchise and the same shape, and it is a different show.

    Counting it would put every season after it one place out, which plays a
    real episode of the wrong series - the failure nothing downstream catches.
    Only the name separates them.
    """
    # Four TV entries would be found if the name were not checked, and the
    # count list has three, so a version that counted it would refuse here
    # rather than answer. Asking for the third season proves it did not.
    assert konosuba.season_address(KONOSUBA_NAMES, 3, 5, [10, 10, 11]) == ("44911", 5)


def test_seasons_that_do_not_line_up_are_refused(konosuba):
    """The correspondence is the check, so no correspondence means no address."""
    assert konosuba.season_address(KONOSUBA_NAMES, 2, 1, [10, 10]) is None
    assert konosuba.season_address(KONOSUBA_NAMES, 2, 1, [12, 12, 12]) is None
    assert konosuba.season_address(KONOSUBA_NAMES, 2, 1, []) is None


def test_an_episode_past_the_end_of_its_season_is_refused(konosuba):
    assert konosuba.season_address(KONOSUBA_NAMES, 1, 11, [10, 10, 11]) is None


def test_a_show_we_cannot_name_is_refused(konosuba):
    assert konosuba.season_address([], 1, 1, [10, 10, 11]) is None
    assert konosuba.season_address(["", ""], 1, 1, [10, 10, 11]) is None


# --------------------------------------------------------------------------
# a show TMDB already numbers from episode one, and counts that disagree
# --------------------------------------------------------------------------

def _node(kitsu_id, title, start, count, subtype="TV", **titles):
    return {"id": kitsu_id, "attributes": {
        "canonicalTitle": title, "titles": titles, "abbreviatedTitles": [],
        "startDate": start, "episodeCount": count, "subtype": subtype}}


HUNTER_SEARCH = {"data": [
    _node("6448", "Hunter x Hunter (2011)", "2011-10-02", 148),
    _node("115", "Hunter x Hunter", "1999-10-16", 62),
    _node("116", "Hunter x Hunter: Yorkshin City Kanketsu-hen", "2002-01-17", 8, "OVA"),
    # Kitsu's text search is loose, and this aired the same year for as long.
    _node("6002", "Toriko", "2011-04-03", 147),
]}


def _kitsu(monkeypatch, answer):
    from pinky.meta import kitsu
    monkeypatch.setattr(kitsu, "_get", lambda path, params=None, **kw: answer(path, params))
    return kitsu


def test_a_series_numbered_from_one_is_addressed_by_its_first_air_year(monkeypatch):
    """TMDB's Hunter x Hunter "2x92" is episode 92 of the 2011 series.
    Torrentio at TMDB's address answered nothing; kitsu:6448:92 answers."""
    kitsu = _kitsu(monkeypatch, lambda path, params: HUNTER_SEARCH)
    assert kitsu.series_address("Hunter x Hunter", 2011, 92) == ("6448", 92)
    assert kitsu.series_address("Hunter x Hunter", 1999, 40) == ("115", 40)


def test_an_episode_past_the_end_of_the_series_has_no_address(monkeypatch):
    kitsu = _kitsu(monkeypatch, lambda path, params: HUNTER_SEARCH)
    assert kitsu.series_address("Hunter x Hunter", 1999, 90) is None


def test_two_shows_of_the_same_name_and_year_give_no_address(monkeypatch):
    """A wrong address plays a real episode of the wrong show."""
    twins = {"data": [_node("1", "Monster", "2004-04-07", 74),
                      _node("2", "Monster", "2004-10-01", 80)]}
    kitsu = _kitsu(monkeypatch, lambda path, params: twins)
    assert kitsu.series_address("Monster", 2004, 10) is None


MONOGATARI_COUR = {"data": [_node("48434", "Monogatari Series: Off & Monster Season",
                                  "2024-07-06", 14)]}
MONOGATARI_EPISODES = {"data": [
    {"attributes": {"number": n, "airdate": date}} for n, date in enumerate(
        ["2024-07-06", "2024-07-13", "2024-07-20", "2024-07-27", "2024-08-03",
         "2024-08-10", "2024-08-24", "2024-08-31", "2024-09-14", "2024-09-21",
         "2024-09-28", "2024-10-05", "2024-10-12", "2024-10-19"], 1)]}


def test_counts_that_disagree_are_resolved_by_the_air_date(monkeypatch):
    """Kitsu has 14 episodes and TMDB 15, the 15th a dateless placeholder.
    Refusing left Monogatari 5x11 with nothing; the date names one episode."""
    kitsu = _kitsu(monkeypatch, lambda path, params: MONOGATARI_EPISODES
                   if path.endswith("/episodes") else MONOGATARI_COUR)
    assert kitsu.episode_address("Monogatari", 11, "OFF & MONSTER Season", 15,
                                 air_date="2024-09-28") == ("48434", 11)
    assert kitsu.episode_address("Monogatari", 11, "OFF & MONSTER Season", 15,
                                 air_date="2025-01-01") is None
    assert kitsu.episode_address("Monogatari", 11, "OFF & MONSTER Season", 15) is None


REZERO_SEARCH = {"data": [
    _node("11209", "Re:Zero kara Hajimeru Isekai Seikatsu", "2016-04-04", 25),
    _node("42903", "Re:Zero kara Hajimeru Isekai Seikatsu: Shin Henshuu-ban",
          "2020-01-01", 13),
    _node("42198", "Re:Zero kara Hajimeru Isekai Seikatsu 2", "2020-07-08", 13),
    _node("43247", "Re:Zero kara Hajimeru Isekai Seikatsu 2nd Season Part 2",
          "2021-01-06", 12),
]}
for _entry, _end in zip(REZERO_SEARCH["data"],
                        ["2016-09-19", "2020-04-01", "2020-09-30", "2021-03-24"]):
    _entry["attributes"]["endDate"] = _end
# Kitsu has no per-episode dates for Part 2 at all.
UNDATED = {"data": [{"attributes": {"number": n, "airdate": None}} for n in range(1, 13)]}
# TMDB's one 85-episode season: 26-38 in 2020, 39-50 from 6 January 2021.
REZERO_DATES = (["2020-07-08"] + ["2020-09-%02d" % d for d in (2, 9, 16, 23, 30)]
                + ["2021-01-%02d" % d for d in (6, 13, 20, 27)])


def test_a_folded_season_is_addressed_by_the_cour_that_aired_that_day(monkeypatch):
    """Re:Zero's TMDB "1x40" matched nothing anywhere; it is the second
    episode of 2nd Season Part 2, counted from TMDB's own air dates because
    Kitsu has none for that cour."""
    kitsu = _kitsu(monkeypatch, lambda path, params: UNDATED
                   if path.endswith("/episodes") else REZERO_SEARCH)
    assert kitsu.air_date_address("Re:Zero kara Hajimeru Isekai Seikatsu",
                                  "2021-01-13", REZERO_DATES) == ("43247", 2)
    assert kitsu.air_date_address("Re:Zero kara Hajimeru Isekai Seikatsu",
                                  "2021-01-06", REZERO_DATES) == ("43247", 1)


def test_a_day_no_cour_covers_has_no_address(monkeypatch):
    kitsu = _kitsu(monkeypatch, lambda path, params: UNDATED
                   if path.endswith("/episodes") else REZERO_SEARCH)
    assert kitsu.air_date_address("Re:Zero kara Hajimeru Isekai Seikatsu",
                                  "2018-05-01", REZERO_DATES) is None


def test_the_search_falls_back_to_the_other_names(monkeypatch):
    """Kitsu cannot find "Re:ZERO -Starting Life in Another World-" and finds
    the romaji name at once."""
    from pinky.sources import aggregator
    asked = []
    found = aggregator._first(
        aggregator._names({"aliases": ["Re:Zero kara Hajimeru Isekai Seikatsu"]},
                          "Re:ZERO -Starting Life in Another World-"),
        lambda name: asked.append(name) or (name.startswith("Re:Zero kara") and ("x", 1)))
    assert found == ("x", 1) and len(asked) == 2


def test_only_a_season_longer_than_a_cour_is_treated_as_folded():
    from pinky.sources import aggregator
    assert aggregator._folded({"season_episodes": 85})
    assert not aggregator._folded({"season_episodes": 25})


def test_the_name_providers_try_the_romaji_name_when_the_english_one_finds_nothing(
        monkeypatch):
    """"Adam's Sweet Agony" found one unrelated batch where "Modaete yo,
    Adam-kun" found twenty-one, and SubsPlease names shows in romaji."""
    from pinky.sources.providers import animetosho, nyaa
    for provider in (nyaa, animetosho):
        asked = []

        def once(meta, asked=asked):
            asked.append(meta["search_title"])
            return ["found"] if meta["search_title"] == "Modaete yo, Adam-kun" else []

        monkeypatch.setattr(provider, "_search_once", once)
        meta = {"type": "episode", "search_title": "Adam's Sweet Agony",
                "aliases": ["Modaete yo, Adam-kun", "A Doce Agonia de Adao"],
                "episode": 7}
        assert provider.search(meta) == ["found"]
        assert asked == ["Adam's Sweet Agony", "Modaete yo, Adam-kun"]


def test_a_name_that_answers_costs_no_second_request(monkeypatch):
    from pinky.sources.providers import nyaa
    asked = []
    monkeypatch.setattr(nyaa, "_search_once",
                        lambda meta: asked.append(1) or ["found"])
    nyaa.search({"type": "episode", "search_title": "Frieren",
                 "aliases": ["Sousou no Frieren"], "episode": 1})
    assert len(asked) == 1


def _jjk(**extra):
    meta = {"type": "episode", "season": 1, "episode": 41, "absolute": 41,
            "title": "JUJUTSU KAISEN", "extra": {"anime": True}}
    meta.update(extra)
    return meta


def _release(title, provider="torrentio"):
    return {"title": title, "provider": provider}


def test_the_releases_numbering_is_read_off_a_folded_season():
    """TMDB's JJK 1x41 is "S2 - 17" to every release and every subtitle, and
    the subtitle search asked for 1x41 and got nothing beside 60 sources."""
    from pinky.sources import aggregator
    meta = _jjk()
    aggregator._scene_episode(meta, [
        _release("[SubsPlease] Jujutsu Kaisen S2 - 17 (1080p) [ABCD1234].mkv"),
        _release("Jujutsu.Kaisen.S02E17.1080p.WEB.H264-VARYG"),
        _release("[Erai-raws] Jujutsu Kaisen 2nd Season - 17 [1080p]", provider="nyaa"),
        _release("Jujutsu Kaisen - 41 [1080p]"),
    ])
    assert meta["scene"] == [2, 17]


def test_no_scene_numbering_without_a_clear_majority_from_id_providers():
    from pinky.sources import aggregator
    meta = _jjk()
    aggregator._scene_episode(meta, [
        _release("Jujutsu.Kaisen.S02E17.1080p.WEB"),
        _release("[Group] Other Show S03E05", provider="nyaa"),
        _release("[Group] Other Show S03E05 v2", provider="animetosho"),
    ])
    assert "scene" not in meta


def test_scene_numbering_is_anime_only():
    from pinky.sources import aggregator
    meta = _jjk(extra={})
    aggregator._scene_episode(meta, [_release("Show.S02E17.1080p")] * 3)
    assert "scene" not in meta


def test_tmdbs_own_numbering_is_not_a_scene_numbering():
    from pinky.sources import aggregator
    meta = _jjk(season=3, episode=5, absolute=60)
    aggregator._scene_episode(meta, [_release("Show.S03E05.1080p")] * 3
                              + [_release("Show - 60 [1080p]")])
    assert "scene" not in meta


def test_the_subtitle_search_asks_under_the_scene_numbering():
    from pinky.subs.providers import common
    assert common.episode_numberings(_jjk(scene=[2, 17])) == [(1, 41), (2, 17)]


def test_a_subtitle_under_the_scene_numbering_is_the_right_episode():
    from pinky.subs import matcher
    candidate = {"release": "Jujutsu Kaisen - S02E17 - Thunderclap, Part 2.eng"}
    wrong = {"release": "Jujutsu Kaisen - S02E18 - Thunderclap, Part 3.eng"}
    without = matcher.target_from(_jjk())
    with_scene = matcher.target_from(_jjk(scene=[2, 17]))
    assert matcher.rate(dict(candidate), without)[0] == 0
    assert matcher.rate(dict(candidate), with_scene)[0] >= 70
    assert matcher.rate(dict(wrong), with_scene)[0] == 0


def test_a_season_with_a_break_in_it_is_folded_after_the_break(monkeypatch):
    """Hell's Paradise season one is 25 episodes - thirteen in 2023, twelve
    in 2026 - so the length rule never fired, and 1x22 found four files
    from a name index where Kitsu's second season has thirty-six."""
    from pinky.sources import aggregator
    dates = (["2023-04-%02d" % day for day in range(1, 14)]
             + ["2026-01-%02d" % day for day in range(1, 13)])
    monkeypatch.setattr(aggregator, "_season_dates", lambda meta: dates)

    def meta(aired):
        return {"season_episodes": 25, "item": {"premiered": aired}}

    assert aggregator._folded(meta("2026-01-09"))
    assert not aggregator._folded(meta("2023-04-13")), \
        "before the break TMDB's own address is the right one"
    assert not aggregator._folded({"season_episodes": 25, "item": {}})


def test_after_a_break_the_numbering_starts_again(monkeypatch):
    """Snow White with the Red Hair 1x21: every release calls it 21, and
    OpenSubtitles files its English as S02E09 - the ninth episode after the
    break in TMDB's own air dates. Nothing before the break is renumbered."""
    from pinky.sources import aggregator
    dates = (["2015-07-%02d" % day for day in range(1, 13)]
             + ["2016-01-%02d" % day for day in range(1, 13)])
    monkeypatch.setattr(aggregator, "_season_dates", lambda meta: dates)

    def meta(aired):
        return {"type": "episode", "season": 1, "episode": 21, "absolute": 21,
                "season_episodes": 24, "extra": {"anime": True},
                "item": {"premiered": aired}}

    after = meta("2016-01-09")
    aggregator._scene_episode(after, [{"title": "[Erai-raws] Show - 21 [1080p]",
                                       "provider": "torrentio"}])
    assert after["scene"] == [2, 9]

    before = meta("2015-07-12")
    aggregator._scene_episode(before, [])
    assert "scene" not in before
