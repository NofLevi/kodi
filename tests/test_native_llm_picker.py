# -*- coding: utf-8 -*-
"""The picker's first page: ten releases for Hebrew, ten for AI.

Asked for as a comparison: the same release can appear in both lists, each row
says NATIVE or LLM in its own colour with how well its subtitle fits, and the
row chosen decides what happens at playback - an LLM row translates without
looking for Hebrew, a NATIVE row uses Hebrew and never turns into AI.
"""
import pytest

from katan import kodi, play
from katan.subs import auto, outlook
from katan.ui import sources_window

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
    return state


def test_every_release_is_judged_for_hebrew_and_for_ai(found):
    sources = [source(EXACT, 1), source(OTHER, 2)]
    native, llm = outlook.split_rows(META, sources)
    assert [row["subs_mode"] for row in native] == ["native", "native"]
    assert native[0]["title"].startswith(EXACT), "best Hebrew fit first"
    assert llm[0]["title"].startswith(OTHER), "best AI source fit first"
    assert llm[0]["subs_from"] == "ar", "Arabic, as playback would choose"
    assert sources[0].get("subs_mode") is None, "rows are copies"


def test_the_same_release_can_be_on_both_lists(found):
    found["llm"] = subs("en", EXACT)
    native, llm = outlook.split_rows(META, [source(EXACT, 1)])
    assert native[0]["hash"] == llm[0]["hash"]


def test_each_list_stops_at_ten(found):
    many = [source(EXACT, i) for i in range(25)]
    native, llm = outlook.split_rows(META, many)
    assert len(native) == outlook.SPLIT_ROWS == 10
    assert len(llm) <= 10


def test_no_ai_rows_without_an_engine(found):
    found["llm"] = []
    native, llm = outlook.split_rows(META, [source(EXACT, 1)])
    assert native and llm == []


def test_a_hebrew_title_is_not_split(found):
    assert outlook.split_rows(dict(META, original_language="he"),
                              [source(EXACT, 1)]) == ([], [])


def test_the_first_page_is_native_then_llm(found, monkeypatch):
    sources = [source(EXACT, 1), source(OTHER, 2)]
    page = sources_window._first_page(META, sources, sources)
    modes = [row["subs_mode"] for row in page]
    assert modes == sorted(modes, key=lambda mode: mode != "native")
    assert "llm" in modes and "native" in modes


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
    assert calls == ["translate"]


def test_a_native_row_never_turns_into_ai(monkeypatch, settings_module):
    """Otherwise a NATIVE choice that fell back to a translation would be
    counted on the wrong side of the comparison."""
    from katan.subs.ai import translator
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
