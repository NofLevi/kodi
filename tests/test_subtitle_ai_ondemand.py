# -*- coding: utf-8 -*-
"""Asking for an AI translation on purpose, and getting one when nothing exists.

Two different requests share this machinery and it is worth keeping them
apart. One is the viewer standing in the subtitle dialog saying "what I have
is wrong, translate it properly" - which has to work *whether or not* a
subtitle in their language was found, because nothing in here can tell a good
Hebrew subtitle from a bad one by looking at it. The other is a film that has
no Hebrew and no English subtitle at all, where the honest answer is not
"none found" but "there is a Spanish one, and it can be translated".
"""
import os

import pytest

from katan.subs import auto, service, srt


MOVIE = {
    "type": "movie",
    "ids": {"imdb": "tt0111161", "tmdb": 278},
    "title": "The Shawshank Redemption",
    "year": 1994,
    "stream_url": "",
    "source": {"release": "Shawshank.1994.1080p.BluRay.x264-AMIABLE",
               "group": "amiable", "quality": "1080p"},
}


def srt_bytes(count=12, text="line"):
    cues = [srt.Cue(i + 1, i * 4.0, i * 4.0 + 2.5, "%s %d" % (text, i))
            for i in range(count)]
    return srt.dump(cues).encode("utf-8")


def candidate(release, language, **kwargs):
    entry = {"provider": "opensubtitles", "language": language,
             "release": release, "download": release}
    entry.update(kwargs)
    return entry


@pytest.fixture
def fake_world(monkeypatch, settings_module):
    """Providers and the translator replaced by things that answer to order."""
    settings_module.set_many({
        "subs.languages": "he,en",
        "subs.threshold": "70",
        "subs.hash_match": "false",
        "subs.provider.wizdom": "true",
        "subs.provider.opensubtitles": "true",
        "subs.ai.enabled": "true",
    })

    state = {"candidates": [], "downloads": {}, "asked_for": [],
             "translated": [], "downloaded": []}

    def fake_search(meta, languages, video_hash="", **kwargs):
        state["asked_for"].append(list(languages))
        return list(state["candidates"])

    def fake_download(cand, expect_language=None):
        state["downloaded"].append(cand.get("download"))
        data = state["downloads"].get(cand.get("download"), b"")
        cues = srt.parse(srt.decode(data)) if data else []
        return srt.clean(cues) if cues else []

    def fake_translate(cues, target_language="he", on_progress=None, meta=None,
                       **kwargs):
        state["translated"].append((target_language, len(cues)))
        return [srt.Cue(c.index, c.start, c.end, "translated %d" % c.index)
                for c in cues]

    from katan.subs.ai import translator
    monkeypatch.setattr(auto, "search_candidates", fake_search)
    monkeypatch.setattr(auto, "download_candidate", fake_download)
    monkeypatch.setattr(translator, "available", lambda: True)
    monkeypatch.setattr(translator, "translate", fake_translate)
    return state


# --------------------------------------------------------------------------
# translating on demand
# --------------------------------------------------------------------------


def test_a_translation_is_produced_even_when_hebrew_already_exists(fake_world):
    """The whole point of the row: a Hebrew subtitle that is simply wrong.

    A perfect-looking Hebrew match is present and the threshold is happily
    cleared, so every automatic path here would stop and use it. The viewer
    asked for a translation anyway, which is a judgement this code cannot
    make for them and must not override.
    """
    name = "Shawshank.1994.1080p.BluRay.x264-AMIABLE"
    fake_world["candidates"] = [candidate(name, "he"), candidate(name, "en")]
    fake_world["downloads"][name] = srt_bytes()

    path = auto.translate_now(MOVIE, "he", video_hash="")

    assert path, "the viewer asked for a translation and did not get one"
    assert fake_world["translated"] == [("he", 12)]
    assert srt.read(path)[0].text == "translated 1"


def test_the_translation_never_overwrites_a_downloaded_subtitle(fake_world):
    """Both files have to be able to sit in the folder at once.

    Otherwise translating replaces the subtitle the viewer was watching, and
    if the translation turns out worse there is nothing to go back to.
    """
    name = "Shawshank.1994.1080p.BluRay.x264-AMIABLE"
    fake_world["candidates"] = [candidate(name, "en")]
    fake_world["downloads"][name] = srt_bytes()

    downloaded = auto.store(MOVIE, "he", srt.parse(srt.decode(srt_bytes())))
    translated = auto.translate_now(MOVIE, "he", video_hash="")

    assert downloaded != translated
    assert srt.read(downloaded)[0].text != srt.read(translated)[0].text


def test_kodi_can_still_tell_what_language_the_translation_is_in():
    """Kodi reads a subtitle's language from the last part before .srt, so
    "ai" cannot be that part or the file is offered as a language called
    "ai"."""
    name = auto.name_for(MOVIE, "he", variant=auto.VARIANT_AI)
    assert name.endswith(".he.srt")
    assert ".ai." in name


def test_an_episode_translation_is_named_per_episode():
    episode = dict(MOVIE, type="episode", season=1, episode=4)
    name = auto.name_for(episode, "he", variant=auto.VARIANT_AI)
    assert "s01e04" in name
    assert name.endswith(".ai.he.srt")


def test_the_search_widens_beyond_the_configured_languages(fake_world):
    """A film with no Hebrew and no English usually has something.

    The automatic path asks for the two languages the viewer configured,
    because it is looking for something to show. This is looking for something
    to translate, and any language will do.
    """
    fake_world["candidates"] = [candidate("Shawshank.1994.1080p", "es")]
    fake_world["downloads"]["Shawshank.1994.1080p"] = srt_bytes()

    path = auto.translate_now(MOVIE, "he", video_hash="")

    assert path
    asked = fake_world["asked_for"][0]
    assert asked[0] == "he"
    for code in ("en", "es", "ar", "ru"):
        assert code in asked, "%s was never asked for" % code


def test_a_dead_download_falls_through_to_the_next_language(fake_world):
    """The one English subtitle listed being a dead link must not end it."""
    fake_world["candidates"] = [
        candidate("dead.release", "en", score=99),
        candidate("live.release", "es", score=95),
    ]
    fake_world["downloads"]["live.release"] = srt_bytes()

    path = auto.translate_now(MOVIE, "he", video_hash="")

    assert path, "a dead English link ended the attempt"
    assert fake_world["translated"] == [("he", 12)]


def test_a_dead_best_candidate_tries_the_next_one_in_the_same_language(fake_world):
    dead = "Shawshank.1994.1080p.BluRay.x264-AMIABLE"
    live = "Shawshank.1994.1080p.BluRay.x264-OTHER"
    spanish = "unrelated.spanish.release"
    fake_world["candidates"] = [candidate(dead, "en"), candidate(live, "en"),
                                candidate(spanish, "es")]
    fake_world["downloads"][live] = srt_bytes()
    fake_world["downloads"][spanish] = srt_bytes()

    path = auto.translate_now(MOVIE, "he", video_hash="")
    assert path
    assert fake_world["downloaded"][:2] == [dead, live]


def test_translation_failure_does_not_reset_budget_on_another_source(
        fake_world, monkeypatch):
    from katan.subs.ai import translator

    first = "Shawshank.1994.1080p.BluRay.x264-AMIABLE"
    second = "Shawshank.1994.1080p.BluRay.x264-OTHER"
    fake_world["candidates"] = [candidate(first, "en"), candidate(second, "en")]
    fake_world["downloads"][first] = srt_bytes()
    fake_world["downloads"][second] = srt_bytes()
    monkeypatch.setattr(
        translator, "translate",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            translator.TranslationBudgetExceeded("budget")))

    assert auto.translate_now(MOVIE, "he", video_hash="") == ""
    assert fake_world["downloaded"] == [first]


def test_translation_source_downloads_never_exceed_operation_budget(fake_world):
    names = ["dead-%d" % index for index in range(5)]
    fake_world["candidates"] = [
        candidate(name, "en", score=100 - index)
        for index, name in enumerate(names)]

    assert auto.translate_now(MOVIE, "he", video_hash="") == ""
    assert fake_world["downloaded"] == names[:3]


def test_no_engine_means_no_promise(fake_world, monkeypatch):
    from katan.subs.ai import translator
    monkeypatch.setattr(translator, "available", lambda: False)
    fake_world["candidates"] = [candidate("x", "en")]
    fake_world["downloads"]["x"] = srt_bytes()

    assert auto.translate_now(MOVIE, "he", video_hash="") == ""


def test_nothing_at_all_to_translate_is_reported_not_crashed(fake_world):
    assert auto.translate_now(MOVIE, "he", video_hash="") == ""


# --------------------------------------------------------------------------
# the automatic path, when there is nothing in either language
# --------------------------------------------------------------------------


def test_a_film_with_no_hebrew_or_english_is_translated_rather_than_refused(
        fake_world):
    """"No subtitles found" was untrue: there was a Spanish one all along."""
    fake_world["candidates"] = [candidate("Shawshank.1994.1080p", "es")]
    fake_world["downloads"]["Shawshank.1994.1080p"] = srt_bytes()

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])

    assert path, report
    assert report["translated"] is True
    assert srt.read(path)[0].text == "translated 1"


def test_genuinely_nothing_still_says_so(fake_world):
    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert path == ""
    assert report["reason"] == "no candidates"


# --------------------------------------------------------------------------
# the row in Kodi's subtitle dialog
# --------------------------------------------------------------------------


def test_the_row_is_offered_even_when_the_list_is_full(fake_world):
    ranked = [{"language": "he", "score": 96, "provider": "wizdom"}]
    entry = service._ai_entry(ranked, "he")
    assert entry is not None
    assert entry["provider"] == service.AI_PROVIDER
    assert entry["language"] == "he"


def test_the_row_is_offered_when_nothing_was_found(fake_world):
    assert service._ai_entry([], "he") is not None


def test_the_row_still_appears_without_a_key_and_says_so(fake_world,
                                                        monkeypatch,
                                                        settings_module):
    """Hiding it until a key exists means the viewer who most needs it - the
    one staring at a film with no subtitles - is shown nothing and never
    learns the feature is there."""
    from katan.subs.ai import translator
    monkeypatch.setattr(translator, "available", lambda: False)
    settings_module.set("subs.ai.enabled", "true")

    entry = service._ai_entry([], "he")

    assert entry is not None
    assert entry["release"] == service.kodi.localize(32497)


def test_no_row_when_ai_is_switched_off(fake_world, settings_module):
    """That is somebody saying they do not want it, which is different from
    not having got round to entering a key."""
    settings_module.set("subs.ai.enabled", "false")
    assert service._ai_entry([], "he") is None


def test_the_row_names_the_language_it_would_translate_from(fake_world):
    ranked = [{"language": "he", "score": 40}, {"language": "es", "score": 88}]
    entry = service._ai_entry(ranked, "he")
    assert "ES" in entry["release"]


def test_the_row_does_not_claim_a_match_score(fake_world):
    """Its accuracy is the accuracy of whatever it ends up translating, which
    is not known yet, so no percentage may be shown."""
    entry = service._ai_entry([], "he")
    assert "%" not in service.match_label(entry)


def test_kodis_own_language_choice_wins_over_the_add_ons(settings_module):
    settings_module.set("subs.languages", "he,en")
    assert service._target_language(["fr", "en"]) == "fr"
    assert service._target_language([]) == "he"


# --------------------------------------------------------------------------
# the hand-off from the dialog to the background service
#
# Both halves of this were found in a real Kodi rather than reasoned about: a
# plugin invocation is torn down the moment it returns, so a thread started
# there dies part way through; and Kodi's subtitle window is modal, so a
# prompt opened from under it never appears at all.
# --------------------------------------------------------------------------


def test_pressing_the_row_leaves_a_request_for_the_service(fake_world):
    from katan import kodi
    kodi.clear_property(service.AI_REQUEST)

    service._translate_with_ai("he")

    assert kodi.get_property(service.AI_REQUEST) == "he"


def test_the_request_falls_back_to_the_configured_language(fake_world,
                                                           settings_module):
    from katan import kodi
    settings_module.set("subs.languages", "he,en")
    kodi.clear_property(service.AI_REQUEST)

    service._translate_with_ai("")

    assert kodi.get_property(service.AI_REQUEST) == "he"


def test_openai_selection_never_launches_the_gemini_wizard(
        fake_world, monkeypatch, settings_module):
    from katan import settings
    from katan.subs.ai import translator
    from katan.ui import wizard

    settings_module.set("subs.ai.engine", "openai")
    monkeypatch.setattr(translator, "available", lambda: False)
    monkeypatch.setattr(
        wizard, "step_ai",
        lambda: pytest.fail("OpenAI setup must not ask for a Gemini key"))
    opened = []
    monkeypatch.setattr(settings, "open_settings", lambda: opened.append(True))

    assert service._engine_ready() is False
    assert opened == [True]


def test_the_service_takes_a_request_once_and_only_once(fake_world):
    """Taking rather than reading is what stops one press starting two."""
    service._translate_with_ai("he")

    assert service.take_request() == "he"
    assert service.take_request() == ""


def test_a_second_press_while_one_runs_says_so(fake_world):
    import xbmcgui
    from katan import kodi
    kodi.set_property(service.AI_RUNNING, "1")
    kodi.clear_property(service.AI_REQUEST)
    del xbmcgui.NOTIFICATIONS[:]
    try:
        service._translate_with_ai("he")
        assert kodi.get_property(service.AI_REQUEST) == "", \
            "a second translation of the same film was queued"
        assert xbmcgui.NOTIFICATIONS, "the viewer was told nothing"
    finally:
        kodi.clear_property(service.AI_RUNNING)


def test_the_running_flag_is_cleared_even_when_it_fails(fake_world,
                                                        monkeypatch):
    """Otherwise one failure means no translation for the rest of the session."""
    from katan import kodi
    monkeypatch.setattr(service, "_current_meta", lambda: dict(MOVIE))
    monkeypatch.setattr(auto, "translate_now",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    kodi.clear_property(service.AI_RUNNING)

    assert service.run_translation("he") == ""
    assert kodi.get_property(service.AI_RUNNING) == ""


def test_the_service_translates_what_the_dialog_asked_for(fake_world,
                                                          monkeypatch):
    name = "Shawshank.1994.1080p.BluRay.x264-AMIABLE"
    fake_world["candidates"] = [candidate(name, "en")]
    fake_world["downloads"][name] = srt_bytes()
    monkeypatch.setattr(service, "_current_meta", lambda: dict(MOVIE))
    monkeypatch.setattr(auto, "video_hash_for", lambda meta: "")

    service._translate_with_ai("he")
    path = service.run_translation(service.take_request())

    assert path
    assert fake_world["translated"] == [("he", 12)]


def test_the_final_translation_replaces_temporary_partial_paths(
        fake_world, monkeypatch):
    import xbmc

    name = "Shawshank.1994.1080p.BluRay.x264-AMIABLE"
    fake_world["candidates"] = [candidate(name, "en")]
    fake_world["downloads"][name] = srt_bytes()
    monkeypatch.setattr(service, "_current_meta", lambda: dict(MOVIE))
    monkeypatch.setattr(auto, "video_hash_for", lambda meta: "")
    shown = []

    class Player(object):
        def setSubtitles(self, path):
            shown.append(path)

        def showSubtitles(self, visible):
            pass

    monkeypatch.setattr(xbmc, "Player", Player)
    path = service.run_translation("he")
    assert path and os.path.isfile(path)
    assert shown[-1] == path


def test_finished_translation_cannot_attach_to_a_new_playback(
        fake_world, monkeypatch):
    import xbmc
    from katan.subs.ai import translator

    old = dict(MOVIE, stream_url="https://cdn/old")
    new = dict(MOVIE, stream_url="https://cdn/new")
    state = {"meta": old}
    monkeypatch.setattr(service, "_current_meta", lambda: state["meta"])
    monkeypatch.setattr(translator, "available", lambda: True)
    shown = []
    calls = []

    class Player(object):
        def setSubtitles(self, path):
            shown.append(path)

        def showSubtitles(self, visible):
            pass

    def translate(meta, target, player=None, **kwargs):
        calls.append(kwargs)
        state["meta"] = new
        if kwargs.get("cancelled"):
            assert kwargs["cancelled"]()
        return "/tmp/old-film.srt"

    monkeypatch.setattr(xbmc, "Player", Player)
    monkeypatch.setattr(auto, "translate_now", translate)
    assert service.run_translation("he") == ""
    assert calls and calls[0].get("cancelled")
    assert shown == []
