# -*- coding: utf-8 -*-
"""What to say when every copy of a film is a camera recording.

The Odyssey (2026), measured on 9 October: 46 sources, all of them cams of a
film five weeks from its digital release, and the add-on said "No playable
sources found". True, and useless - the only reading left was that it was
broken. Say what the copies are, give the date the real one arrives, and let
the viewer decide.
"""
import time

import pytest

from pinky import cache, kodi, play
from pinky.sources import aggregator, model

SOON = time.strftime("%Y-%m-%d", time.localtime(time.time() + 40 * 86400))
IN_CINEMAS = time.strftime("%Y-%m-%d", time.localtime(time.time() - 80 * 86400))

FILM = {"type": "movie", "year": int(time.strftime("%Y")), "title": "האודיסאה",
        "original_title": "The Odyssey", "ids": {"imdb": "tt33764258"},
        "item": {"extra": {"cinema": IN_CINEMAS, "home": SOON}}}


def cam(title):
    source = model.from_release_name(title, provider="test",
                                     info_hash="%040x" % abs(hash(title)),
                                     size=3 * 1024 ** 3, seeders=30)
    source["cached"] = True
    return source


@pytest.fixture
def nothing_but_cams(settings_module):
    settings_module.set("sources.allow_cam", "false")
    found = [cam("The Odyssey 2026 1080p TELESYNC x264-SPLiCE"),
             cam("The Odyssey 2026 HDTS 720p x264"),
             cam("The.Odyssey.2026.CAM.1080p")]
    cache.volatile_set(aggregator.unfiltered_key(FILM), found, 60)
    return found


def test_it_says_what_the_copies_are_and_when_the_real_one_arrives(
        nothing_but_cams, monkeypatch):
    asked = {}
    monkeypatch.setattr(kodi, "yes_no",
                        lambda message, heading="", **kw:
                        asked.setdefault("message", message) is None or True)
    play._offer_cams(dict(FILM))
    assert SOON in asked["message"], asked["message"]
    assert "3" in asked["message"], "it should say how many copies there are"


def test_saying_yes_hands_back_the_cams(nothing_but_cams, monkeypatch):
    monkeypatch.setattr(kodi, "yes_no", lambda *a, **k: True)
    offered = play._offer_cams(dict(FILM))
    assert len(offered) == 3
    assert all(s["extra"]["playback_allow_cam"] for s in offered)


def test_saying_no_plays_nothing(nothing_but_cams, monkeypatch):
    monkeypatch.setattr(kodi, "yes_no", lambda *a, **k: False)
    assert play._offer_cams(dict(FILM)) == []


def test_consent_is_for_this_playback_and_not_the_setting(
        nothing_but_cams, monkeypatch, settings_module):
    monkeypatch.setattr(kodi, "yes_no", lambda *a, **k: True)
    play._offer_cams(dict(FILM))
    assert settings_module.get("sources.allow_cam") == "false", \
        "a film in cinemas must not turn cams on for every film after it"


def test_a_film_with_real_releases_is_never_offered_cams(settings_module):
    """The offer exists for the case where there is nothing else at all."""
    real = model.from_release_name("The Odyssey 2026 1080p WEB-DL x264-GRP",
                                   provider="test", info_hash="b" * 40,
                                   size=4 * 1024 ** 3, seeders=50)
    real["cached"] = True
    film = dict(FILM, item={"extra": {"cinema": IN_CINEMAS, "home": "2000-01-01"}})
    cache.volatile_set(aggregator.unfiltered_key(film), [real], 60)
    assert play._offer_cams(film) == []


def test_nothing_found_at_all_is_not_an_offer(settings_module):
    empty = dict(FILM, ids={"imdb": "tt00000000"})
    cache.volatile_set(aggregator.unfiltered_key(empty), [], 60)
    assert play._offer_cams(empty) == []
