# -*- coding: utf-8 -*-
"""What a tracker files under a film's id that is not the film.

Measured on The Odyssey (2026), 9 October 2026. Its 46 sources arrived under
the film's own IMDb id and included, from Torrentio, "the Making of an Epic",
"The Odyssey Prologue", and one an uploader had named "NOT The Chris Nolan
FILM"; TorrentsDB added episodes of a television series. Every one of them
was kept and any of them would play - which is what "it loaded the wrong
movie" was.
"""
from pinky.sources import model, scoring

FILM = {"type": "movie", "year": 2026, "title": "האודיסאה",
        "original_title": "The Odyssey", "ids": {"imdb": "tt33764258"}}


def make(title):
    return model.from_release_name(title, provider="test",
                                   info_hash="%040x" % abs(hash(title)),
                                   size=4 * 1024 ** 3, seeders=20)


def reason(title, meta=FILM):
    return scoring._wrong_work(make(title), meta)


def test_the_documentary_about_the_film_is_not_the_film():
    assert reason("The Odyssey (2026) the Making of an Epic")
    assert reason("The Odyssey 2026 1080p Behind the Scenes")


def test_a_prologue_is_not_the_film():
    assert reason("The.Odyssey.Prologue.2025.IMAX.2D.4K.ProRes-hutaoing.mov")


def test_an_uploader_saying_it_is_another_film_is_believed():
    assert reason("The Odyssey 2026 NOT The Chris Nolan FILM 1080p WEB-DL-BONE.mkv")


def test_episodes_of_a_series_are_not_a_film():
    assert reason("ODY.1x01 Iliad.mp4")
    assert reason("ODY.2x01 On the Knees of the Gods.mp4")


def test_the_film_itself_survives_all_of_it():
    for title in ("The Odyssey 2026 1080p AMZN WEB-DL DDP5 1 H 264-Kitsune.mkv",
                  "The.Odyssey.2026.1080p.WEBRip.x265-INFINITY.mp4",
                  "The Odyssey (2026) (1080p AMZN WEB-DL x265 Celdra).mkv",
                  "Одиссея (The Odyssey 2026).mkv"):
        assert reason(title) == "", title


def test_a_film_whose_own_name_is_the_word_is_still_findable():
    """"Prologue" condemns a release only when it is not what was asked for."""
    prologue = {"type": "movie", "year": 2015, "title": "Prologue",
                "original_title": "Prologue"}
    assert scoring._wrong_work(make("Prologue.2015.1080p.BluRay.x264-GRP"),
                               prologue) == ""


def test_an_episode_is_still_an_episode_when_an_episode_was_asked_for():
    series = {"type": "episode", "year": 2026, "original_title": "Odyssey",
              "season": 1, "episode": 1}
    assert scoring._wrong_work(make("ODY.1x01 Iliad.mp4"), series) == ""


def test_a_year_in_a_film_title_is_not_a_production_year():
    """"Blade Runner 2049" parses its own title as the year, which is why the
    same-name year rule stays off for films."""
    meta = {"type": "movie", "year": 2017, "original_title": "Blade Runner 2049"}
    source = make("Blade Runner 2049 2017 1080p BluRay x264-GRP")
    assert scoring._another_production(source, meta) == ""
    assert scoring._wrong_work(source, meta) == ""


def test_rank_refuses_them_and_says_why(settings_module):
    settings_module.set("sources.cached_only", "false")
    kept, rejected = scoring.rank(
        [make("The Odyssey (2026) the Making of an Epic"),
         make("ODY.1x01 Iliad.mp4"),
         make("The.Odyssey.2026.1080p.WEBRip.x265-INFINITY.mp4")], dict(FILM))
    assert [s["title"] for s in kept] == [
        "The.Odyssey.2026.1080p.WEBRip.x265-INFINITY.mp4"]
    assert rejected.get("an extra, or another work, rather than the film") == 1
    assert rejected.get("an episode of a series, not the film") == 1


def test_the_download_offer_is_judged_like_the_picker(monkeypatch, settings_module):
    """"Start one downloading" built its list from the unfiltered results and
    asked only the settings, so everything the picker had refused was offered
    back - which is how the wrong film came to play."""
    from pinky import cache
    from pinky.sources import aggregator

    sources = [make("The Odyssey (2026) the Making of an Epic"),
               make("ODY.1x01 Iliad.mp4"),
               make("The Odyssey 2026 NOT The Chris Nolan FILM 1080p WEB-DL-BONE.mkv"),
               make("The.Odyssey.2026.1080p.WEBRip.x265-INFINITY.mp4")]
    for source in sources:
        source["cached"] = False
    cache.volatile_set(aggregator.unfiltered_key(FILM), sources, 60)
    offered = aggregator.uncached(dict(FILM))
    assert [s["title"] for s in offered] == [
        "The.Odyssey.2026.1080p.WEBRip.x265-INFINITY.mp4"]
