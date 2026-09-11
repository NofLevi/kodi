# -*- coding: utf-8 -*-
"""A release of a different production that shares the title.

Asked for episode 1 of the 2001 Hikaru no Go anime, Torrentio answered with
"Hikaru no Go (2020) - 01 [WEB 1080p]" - the Chinese live-action drama - under
the anime's own IMDb address, and it ranked third. The year right after the
title is the release saying which production it is.
"""
from katan.sources import model, scoring

REASON = "another production of the same name"

HIKARU = {"type": "episode", "year": 2001, "title": "היקארו נו גו",
          "original_title": "ヒカルの碁", "search_title": "Hikaru no Go",
          "season": 1, "episode": 1}


def make(title):
    return model.from_release_name(title, provider="test",
                                   info_hash="%040x" % abs(hash(title)),
                                   size=1024 ** 3, seeders=10)


def reason(title, meta=HIKARU):
    return scoring._another_production(make(title), meta)


def test_the_live_action_drama_is_not_the_anime():
    assert reason("Hikaru no Go (2020) - 01 [WEB 1080p] [FEDD12FE].mkv") == REASON


def test_with_a_group_in_front_as_well():
    assert reason("[Group] Hikaru no Go (2020) - 01 [1080p].mkv") == REASON


def test_the_anime_itself_is_kept():
    assert reason("Hikaru.No.Go.TV.EP01.BluRay.1080p.AC3.x264-CHD.mkv") == ""
    assert reason("[BlueLobster] Hikaru no Go - 01 [480p].mkv") == ""
    assert reason("Hikaru no Go (2001) - 01 [BD 1080p].mkv") == ""


def test_a_year_a_year_off_is_the_same_production():
    """TMDB's year and the one a release carries differ by one often
    enough - a late-December premiere, a different country's broadcast."""
    assert reason("Hikaru no Go (2002) - 01 [1080p].mkv") == ""


def test_a_year_after_the_episode_is_not_the_show_s():
    meta = {"type": "episode", "year": 2014, "original_title": "Fargo"}
    assert scoring._another_production(
        make("Fargo.S05E01.2023.1080p.WEB-DL-GRP"), meta) == ""


def test_a_daily_show_s_air_date_is_not_a_year_of_production():
    meta = {"type": "episode", "year": 1996,
            "original_title": "The Daily Show"}
    assert scoring._another_production(
        make("The.Daily.Show.2024.03.01.1080p.WEB.h264-GRP"), meta) == ""


def test_a_title_that_is_a_year_is_read_past():
    meta = {"type": "episode", "year": 2017, "original_title": "1983"}
    assert scoring._another_production(
        make("1983.2018.S01E01.1080p.WEB-GRP"), meta) == ""
    assert scoring._another_production(
        make("1983.2031.S01E01.1080p.WEB-GRP"), meta) == REASON


def test_films_are_left_alone():
    """A film's TMDB year and its release year are two apart often enough
    that the same rule would throw real releases away."""
    meta = {"type": "movie", "year": 2021, "original_title": "Dune"}
    assert scoring._another_production(
        make("Dune.1984.1080p.BluRay.x264-GRP"), meta) == ""


def test_rank_reports_it(settings_module):
    settings_module.set("sources.cached_only", "false")
    kept, rejected = scoring.rank(
        [make("Hikaru no Go (2020) - 01 [WEB 1080p] [FEDD12FE].mkv"),
         make("Hikaru.No.Go.TV.EP01.BluRay.1080p.AC3.x264-CHD.mkv")],
        dict(HIKARU))
    assert [s["title"] for s in kept] == [
        "Hikaru.No.Go.TV.EP01.BluRay.1080p.AC3.x264-CHD.mkv"]
    assert rejected.get(REASON) == 1
