# -*- coding: utf-8 -*-
"""The picker's first page: ten releases for Hebrew, ten for AI.

Asked for as a comparison: the same release can appear in both lists, each row
says NATIVE or LLM in its own colour with how well its subtitle fits, and the
row chosen decides what happens at playback - an LLM row translates without
looking for Hebrew, a NATIVE row uses Hebrew and never turns into AI.
"""
import pytest

from pinky import kodi, play
from pinky.subs import auto, outlook
from pinky.ui import sources_window

META = {"type": "movie", "title": "A Film", "year": 2020,
        "ids": {"imdb": "tt1", "tmdb": 1}, "original_language": "en"}
EXACT = "A.Film.2020.1080p.WEB-DL.x264-GRP"
OTHER = "A.Film.2020.2160p.BluRay.x265-OTHER"


def source(title, index, **extra):
    entry = {"title": title + ".mkv", "hash": "%040d" % index, "provider": "torrentio",
             "quality": "1080p", "group": "", "languages": [], "cached": True}
    entry.update(extra)
    return entry


def subs(language, *names):
    return [{"name": name, "release": name, "language": language,
             "provider": "opensubtitles_rest", "id": language + name}
            for name in names]


@pytest.fixture
def found(monkeypatch):
    state = {"he": subs("he", EXACT), "llm": subs("en", OTHER) + subs("ar", OTHER)}
    monkeypatch.setattr(outlook, "candidates", lambda meta, **k: state["he"])
    monkeypatch.setattr(outlook, "translation_candidates", lambda meta: state["llm"])
    monkeypatch.setattr(outlook, "english_candidates",
                        lambda meta, already=None: [c for c in state["llm"]
                                                    if c["language"] == "en"])
    return state


def test_every_release_is_judged_for_hebrew_and_for_ai(found):
    sources = [source(EXACT, 1), source(OTHER, 2)]
    native, llm, _english = outlook.split_rows(META, sources)
    assert [row["subs_mode"] for row in native] == ["native", "native"]
    assert native[0]["title"].startswith(EXACT), "best Hebrew fit first"
    assert llm[0]["title"].startswith(OTHER), "best AI source fit first"
    assert llm[0]["subs_from"] == "ar", "Arabic, as playback would choose"
    assert sources[0].get("subs_mode") is None, "rows are copies"


def test_the_same_release_can_be_on_both_lists(found):
    found["llm"] = subs("en", EXACT)
    native, llm, _english = outlook.split_rows(META, [source(EXACT, 1)])
    assert native[0]["hash"] == llm[0]["hash"]


def test_each_list_stops_at_ten(found):
    many = [source(EXACT, i) for i in range(25)]
    native, llm, _english = outlook.split_rows(META, many)
    assert len(native) == outlook.SPLIT_ROWS == 10
    assert len(llm) <= 10


def test_no_ai_rows_without_an_engine(found):
    found["llm"] = []
    native, llm, _english = outlook.split_rows(META, [source(EXACT, 1)])
    assert native and llm == []


def test_a_hebrew_title_is_not_split(found):
    assert outlook.split_rows(dict(META, original_language="he"),
                              [source(EXACT, 1)]) == ([], [], [])


ORDER = {"native": 0, "llm": 1, "english": 2}


def test_the_first_page_is_hebrew_then_llm_then_english(found):
    """It used to be three lists end to end, and read like it: on The Odyssey
    the routes ran LLM, LLM, LLM, ENGLISH five times, then LLM again, so one
    release sat at row one and row seven with different labels and nothing in
    the sequence said why. Every route every release has is still its own
    row - the comparison is the point - but the page answers "which subtitle
    fits best" from the top down."""
    sources = [source(EXACT, 1), source(OTHER, 2)]
    page = sources_window._first_page(META, sources, sources)
    modes = [row["subs_mode"] for row in page if row.get("subs_mode")]

    assert modes == sorted(modes, key=ORDER.get), "the ladder, not a scoreboard"
    assert set(modes) == {"native", "llm", "english"}


def test_hebrew_wins_even_when_a_translation_fits_better(found):
    """Measured on The Odyssey: three LLM rows at 76% sat above seven NATIVE
    rows at 70%, which reads as "translate this" on a film that has a Hebrew
    subtitle somebody made. The fit orders the rungs of the ladder; it does
    not reorder the ladder."""
    rows = [{"subs_mode": "english", "subs_fit": 99},
            {"subs_mode": "llm", "subs_fit": 76},
            {"subs_mode": "native", "subs_fit": 70},
            {"subs_mode": "llm", "subs_fit": 90}]
    ordered = sources_window._in_one_order(rows)

    assert [r["subs_mode"] for r in ordered] ==         ["native", "llm", "llm", "english"],         "a Hebrew subtitle at 70 still beats a translation at 90"
    assert [r["subs_fit"] for r in ordered] == [70, 90, 76, 99],         "and the fit orders the rungs"


def test_no_hebrew_anywhere_still_opens_on_llm_and_english(found):
    """Measured: 5 of 7 anime and both Turkish dramas tested had no Hebrew
    subtitle at all. Those are exactly what this page is for."""
    found["he"] = []
    sources = [source(EXACT, 1), source(OTHER, 2)]
    page = sources_window._first_page(META, sources, sources)
    modes = [row["subs_mode"] for row in page]
    assert "native" not in modes
    assert modes[0] == "llm" and "english" in modes


def test_english_rows_stop_at_five(found):
    found["llm"] = subs("en", EXACT)
    _native, _llm, english = outlook.split_rows(META, [source(EXACT, i) for i in range(12)])
    assert len(english) == outlook.ENGLISH_ROWS == 5
    assert all(row["subs_mode"] == "english" for row in english)


def test_english_rows_need_no_translation_engine(monkeypatch):
    monkeypatch.setattr(outlook, "candidates", lambda meta, **k: [])
    monkeypatch.setattr(outlook, "translation_candidates", lambda meta: [])
    monkeypatch.setattr(outlook, "english_candidates",
                        lambda meta, already=None: subs("en", EXACT))
    native, llm, english = outlook.split_rows(META, [source(EXACT, 1)])
    assert native == [] and llm == [] and english


def test_nothing_to_split_leaves_the_plain_list(found):
    found["he"], found["llm"] = [], []
    sources = [source(EXACT, 1)]
    assert sources_window._first_page(META, sources, sources) == sources


def test_the_rows_say_which_kind_they_are_in_different_colours():
    native = sources_window._subtitle_badge(
        {"subs_mode": "native", "subs_fit": 82})
    llm = sources_window._subtitle_badge(
        {"subs_mode": "llm", "subs_fit": 91, "subs_from": "ar"})
    assert kodi.localize(32539) in native and "82" in native
    assert kodi.localize(32540) in llm and "AR" in llm and "91" in llm
    assert sources_window.MODE_COLOURS["native"] in native
    assert sources_window.MODE_COLOURS["llm"] in llm
    assert sources_window.MODE_COLOURS["native"] != sources_window.MODE_COLOURS["llm"]
    english = sources_window._subtitle_badge({"subs_mode": "english", "subs_fit": 77})
    assert kodi.localize(32543) in english and "77" in english
    assert len(set(sources_window.MODE_COLOURS.values())) == 3


def test_the_chosen_row_travels_to_playback():
    record = play.source_record(dict(source(EXACT, 1), subs_mode="llm"))
    assert record["subs_mode"] == "llm"


def test_an_llm_row_translates_without_looking_for_hebrew(monkeypatch, settings_module):
    settings_module.set_many({"subs.auto": "true", "subs.languages": "he,en",
                              "subs.embedded_first": "true"})
    calls = []
    monkeypatch.setattr(auto, "find_and_prepare",
                        lambda *a, **k: calls.append("hebrew search") or ("", {}))
    monkeypatch.setattr(auto, "use_embedded",
                        lambda *a, **k: calls.append("embedded") or False)
    monkeypatch.setattr(auto, "translate_now",
                        lambda *a, **k: calls.append("translate") or "")
    monkeypatch.setattr(outlook, "translation_candidates", lambda meta: [])
    auto.on_playback_started(object(), dict(META, source={"subs_mode": "llm"}))
    assert "hebrew search" not in calls, "the row said translate, not search"
    assert calls[0] == "translate"


def test_a_failed_translation_falls_back_to_the_file_s_own_track(
        monkeypatch, settings_module):
    """A blank screen in front of a subtitle is not what the row promised.

    Measured on Black Lagoon 1x12: the picker offered the AI row at 100%, the
    source could not be downloaded, the log said "every translation source
    failed to download", and the viewer switched the embedded English track
    on by hand.
    """
    settings_module.set_many({"subs.auto": "true", "subs.languages": "he,en"})
    tried = []
    monkeypatch.setattr(auto, "find_and_prepare",
                        lambda *a, **k: tried.append("hebrew search") or ("", {}))
    monkeypatch.setattr(auto, "translate_now", lambda *a, **k: "")
    monkeypatch.setattr(auto, "use_embedded",
                        lambda player, code, cancelled=None:
                        tried.append(code) or (code == "en"))
    monkeypatch.setattr(outlook, "translation_candidates", lambda meta: [])

    auto.on_playback_started(object(), dict(META, source={"subs_mode": "llm"}))
    assert tried == ["he", "en"], tried
    assert "hebrew search" not in tried, "still no provider search for Hebrew"


def test_a_successful_translation_leaves_the_file_s_track_alone(
        monkeypatch, settings_module):
    settings_module.set_many({"subs.auto": "true", "subs.languages": "he,en"})
    tried = []
    monkeypatch.setattr(auto, "translate_now", lambda *a, **k: "/tmp/he.srt")
    monkeypatch.setattr(auto, "use_embedded",
                        lambda player, code, cancelled=None: tried.append(code))
    monkeypatch.setattr(outlook, "translation_candidates", lambda meta: [])

    auto.on_playback_started(object(), dict(META, source={"subs_mode": "llm"}))
    assert tried == []


def test_a_native_row_never_turns_into_ai(monkeypatch, settings_module):
    """Otherwise a NATIVE choice that fell back to a translation would be
    counted on the wrong side of the comparison."""
    from pinky.subs.ai import translator
    settings_module.set_many({"subs.languages": "he,en", "subs.threshold": "70"})
    monkeypatch.setattr(translator, "available", lambda: True)
    monkeypatch.setattr(auto, "video_hash_later", lambda meta: (lambda: ""))
    monkeypatch.setattr(auto, "search_candidates",
                        lambda meta, languages, *a, **k: subs("he", OTHER) + subs("en", EXACT))
    monkeypatch.setattr(auto, "download_candidate", lambda *a, **k: [])
    translated = []
    monkeypatch.setattr(auto, "translate_fallback",
                        lambda *a, **k: translated.append(1) or ("", {}))
    monkeypatch.setattr(auto, "translate_now",
                        lambda *a, **k: translated.append(1) or "")
    meta = dict(META, source={"subs_mode": "native", "release": EXACT})
    path, report = auto.find_and_prepare(meta, ["he", "en"])
    assert translated == []
    assert report["reason"].startswith("native row")


def test_an_english_row_plays_english_and_never_translates(monkeypatch, settings_module):
    settings_module.set_many({"subs.auto": "true", "subs.languages": "he,en",
                              "subs.embedded_first": "false"})
    asked = []
    monkeypatch.setattr(auto, "find_and_prepare",
                        lambda meta, languages, *a, **k: asked.append(list(languages)) or ("", {}))
    monkeypatch.setattr(auto, "translate_now",
                        lambda *a, **k: asked.append("translate") or "")
    auto.on_playback_started(object(), dict(META, source={"subs_mode": "english"}))
    assert asked == [["en"]], "even for a film made in English"


def test_the_player_offers_ai_into_hebrew_first_and_english_second(settings_module):
    from pinky.subs import service
    settings_module.set("subs.languages", "he,en")
    assert service.ai_targets() == ["he", "en"]


def test_equal_fit_and_resolution_puts_the_smaller_file_first(found):
    """In every list: with the same fit and resolution, the lower storage."""
    found["llm"] = subs("en", EXACT) + subs("ar", EXACT)
    big = source(EXACT, 1, size=8 * 1024 ** 3)
    small = source(EXACT, 2, size=2 * 1024 ** 3)
    for rows in outlook.split_rows(META, [big, small]):
        assert [row["hash"] for row in rows] == [small["hash"], big["hash"]]


def test_resolution_still_comes_before_size(found):
    found["llm"] = subs("en", EXACT)
    small_720 = source(EXACT, 1, quality="720p", size=1 * 1024 ** 3)
    big_1080 = source(EXACT, 2, quality="1080p", size=8 * 1024 ** 3)
    native, _llm, _english = outlook.split_rows(META, [small_720, big_1080])
    assert native[0]["hash"] == big_1080["hash"]



def test_the_rows_past_the_first_page_still_say_how_they_get_hebrew():
    """Every release is judged three times and the answers used to be thrown
    away with the rows that did not fit, so past the cut the picker read
    "Subtitles 81% estimate" where the rows above read NATIVE or LLM - one
    screen answering the same question in two languages. Measured on Top Gun:
    Maverick, five tagged rows and seventy-seven untagged."""
    many = [source(EXACT, index) for index in range(outlook.SPLIT_ROWS + 4)]
    native, llm, english, rest = outlook.split_rows(META, many, want_rest=True)

    assert len(native) == outlook.SPLIT_ROWS, "the cut still happens"
    assert rest, "and what it cut still exists"
    assert all(row.get("subs_mode") for row in rest), \
        "a row past the cut with no mode is the bug this is about"
    first_page = set(id(r) for rows in (native, llm, english) for r in rows)
    assert not any(id(r) in first_page for r in rest), "nothing listed twice"


def test_a_release_shows_its_best_route_rather_than_the_first_one_found():
    """Native beats LLM beats English at the same fit, because a Hebrew
    subtitle somebody made beats one a model makes - but a poor Hebrew match
    must not be sold as Hebrew over an excellent translatable one."""
    poor = {"subs_mode": "native", "subs_fit": 40}
    good = {"subs_mode": "llm", "subs_fit": 95}
    assert outlook._best_mode([poor, good]) is good

    same_native = {"subs_mode": "native", "subs_fit": 80}
    same_llm = {"subs_mode": "llm", "subs_fit": 80}
    assert outlook._best_mode([same_llm, same_native]) is same_native


def test_a_hebrew_title_has_no_rest_either():
    hebrew = dict(META, original_language="he")
    assert outlook.split_rows(hebrew, [source(EXACT, 1)], want_rest=True) == \
        ([], [], [], [])
