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


def test_a_hebrew_title_still_gets_its_hebrew_rows(found):
    """It used to get no rows at all, on the reasoning that an Israeli film
    is already in Hebrew and a subtitle puts the dialogue on screen twice -
    true of a film wholly in Hebrew, false of the ones this household
    watches. Fauda is half in Arabic; Shtisel has Yiddish in it.

    Measured over 36 titles, these were the only two where the picker drew
    less than what was there: Fauda had five Hebrew subtitles and a ceiling
    of 99, Shtisel two and a ceiling of 100, and both screens were empty.
    """
    native, llm, english = outlook.split_rows(
        dict(META, original_language="he"), [source(EXACT, 1)])

    assert native, "the Hebrew rows are the whole point for an Israeli show"
    assert llm == [], "translating into Hebrew for a Hebrew show answers nothing"
    assert english == []


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
    # Distinct, not three: the HEB DUB rung added a fourth, and what
    # this line is for is that no two rungs share a colour.
    assert (len(set(sources_window.MODE_COLOURS.values()))
            == len(sources_window.MODE_COLOURS))


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








def test_the_page_is_ten_ten_and_five_and_no_more(found):
    """Asked for in so many words, and the cap is the point. Everything that
    passed the filters was drawn for a while - 118 rows on Toy Story 5, most
    of them a release nobody would choose, with the three lists that are the
    whole page buried inside them."""
    many = [source(EXACT, index) for index in range(40)]
    page = sources_window._first_page(META, many[:6], many)
    counts = {}
    for row in page:
        counts[row["subs_mode"]] = counts.get(row["subs_mode"], 0) + 1

    assert counts["native"] == outlook.SPLIT_ROWS
    assert counts["llm"] == outlook.SPLIT_ROWS
    assert counts["english"] == outlook.ENGLISH_ROWS
    assert len(page) == outlook.SPLIT_ROWS * 2 + outlook.ENGLISH_ROWS


def test_a_better_fit_outranks_a_worse_cached_one():
    """Cached used to lead, absolute, so an uncached release with a 100% fit
    sorted below a cached one at 40% - on a page whose whole purpose is
    showing which release gets Hebrew right. Measured after: fit decides
    first, cached only breaks a tie."""
    from pinky.subs import outlook

    worse_cached = {"cached": True, "quality": "1080p", "size": 1}
    better_uncached = {"cached": False, "quality": "1080p", "size": 1}

    key_worse = outlook._order(worse_cached, 40, 0)
    key_better = outlook._order(better_uncached, 100, 1)

    assert key_better < key_worse, "the better fit has to sort first"


def test_cached_still_breaks_a_tie_on_equal_fit():
    from pinky.subs import outlook

    cached = {"cached": True, "quality": "1080p", "size": 1}
    uncached = {"cached": False, "quality": "1080p", "size": 1}

    key_cached = outlook._order(cached, 70, 0)
    key_uncached = outlook._order(uncached, 70, 1)

    assert key_cached < key_uncached, "an equal fit still starts sooner cached"


# --------------------------------------------------------------------------
# a subtitle shipped with the release fits it by construction
# --------------------------------------------------------------------------


def _ships(monkeypatch, known):
    from pinky.sources import bundled
    monkeypatch.setattr(bundled, "recall",
                        lambda source: known.get(source["hash"]))


def test_a_release_shipping_english_is_a_hundred_on_the_english_list(
        found, monkeypatch):
    """The fit used to quote a stranger's upload while the file beside the
    video, in time by construction, only got a mark."""
    _ships(monkeypatch, {"%040d" % 2: ["en"]})
    _native, _llm, english = outlook.split_rows(
        META, [source(EXACT, 1), source(OTHER, 2)])
    fits = {row["hash"]: row["subs_fit"] for row in english}
    assert fits["%040d" % 2] == 100
    assert fits["%040d" % 1] < 100, "only the release that ships it"


def test_a_release_shipping_hebrew_is_a_hundred_on_the_hebrew_list(
        found, monkeypatch):
    found["he"] = []
    _ships(monkeypatch, {"%040d" % 1: ["he"]})
    native, _llm, _english = outlook.split_rows(META, [source(EXACT, 1)])
    assert native and native[0]["subs_fit"] == 100


def test_an_unlabelled_file_is_english_for_anime_and_nothing_otherwise(
        found, monkeypatch):
    """A subtitle beside a fansub release is English; beside a film it
    could be anything, and nothing is claimed."""
    found["llm"] = []
    _ships(monkeypatch, {"%040d" % 1: ["und"]})
    anime = dict(META, type="episode", season=1, episode=1, extra={"anime": True})
    _n, _l, english = outlook.split_rows(anime, [source(EXACT, 1)])
    assert english and english[0]["subs_fit"] == 100
    _n, _l, english = outlook.split_rows(META, [source(EXACT, 1)])
    assert not english


def test_a_release_not_looked_inside_yet_is_scored_as_before(found, monkeypatch):
    _ships(monkeypatch, {})
    _n, _l, english = outlook.split_rows(META, [source(EXACT, 1), source(OTHER, 2)])
    fits = {row["hash"]: row["subs_fit"] for row in english}
    assert fits["%040d" % 1] < 100


def test_a_track_inside_the_file_counts_for_english_but_never_for_ai(
        found, monkeypatch):
    """Kodi shows a muxed track directly, so the English row is honest. The
    translator needs a subtitle it can download and cannot read the track,
    so an AI row built on it would promise what playback cannot do."""
    from pinky.sources import bundled
    from pinky.subs.ai import translator
    monkeypatch.setattr(translator, "available", lambda: True)
    found["llm"] = []
    _ships(monkeypatch, {})
    monkeypatch.setattr(bundled, "recall_inside",
                        lambda source: ["en"] if source["hash"] == "%040d" % 1 else None)
    _native, llm, english = outlook.split_rows(META, [source(EXACT, 1)])
    assert english and english[0]["subs_fit"] == 100
    assert not llm


def test_a_hebrew_track_inside_the_file_is_a_hundred_on_the_hebrew_list(
        found, monkeypatch):
    from pinky.sources import bundled
    found["he"] = []
    _ships(monkeypatch, {})
    monkeypatch.setattr(bundled, "recall_inside", lambda source: ["he"])
    native, _llm, _english = outlook.split_rows(META, [source(EXACT, 1)])
    assert native and native[0]["subs_fit"] == 100


def test_a_release_with_no_subtitle_known_is_still_on_the_page(found, monkeypatch):
    """Hikaru no Go 3x02 kept six releases and drew one: only one had a
    subtitle anybody had indexed, and the other five - one shipping 76
    English .srt files, the rest MKVs with English inside - vanished."""
    found["he"] = []
    found["llm"] = []
    only = source(EXACT, 1)
    others = [source("Hikaru.No.Go.TV.EP62.BluRay.1080p.AC3.x264-CHD", 2),
              source("[BlueLobster] Hikaru no Go - 62 [480p]", 3)]
    _ships(monkeypatch, {only["hash"]: ["he"]})
    page = sources_window._first_page(META, [only] + others, [only] + others)
    assert [row["hash"] for row in page] == [only["hash"], others[0]["hash"],
                                             others[1]["hash"]]
    assert "subs_mode" not in page[-1], "a plain row, claiming no subtitle"


def test_nothing_is_called_hidden_that_was_not_rejected(monkeypatch):
    """"of 10, 9 hidden ()" - found minus drawn, with no reason behind it."""
    from pinky.sources import aggregator
    monkeypatch.setattr(aggregator, "filter_report",
                        lambda meta: {"found": 10, "reasons": []})
    assert sources_window._why_hidden([source(EXACT, 1)], META) == ""
    monkeypatch.setattr(aggregator, "filter_report",
                        lambda meta: {"found": 10, "reasons": [("cam release", 3)]})
    assert "3" in sources_window._why_hidden([source(EXACT, 1)], META)


def test_a_wider_language_is_asked_only_when_the_usual_ones_find_nothing(monkeypatch):
    """Sabrina, the Teenage Witch season 6 has German, Portuguese, Swedish and
    Serbian subtitles and none in the six usual languages - the picker had
    no way to Hebrew at all."""
    from pinky import cache
    asked = []

    def search(meta, languages):
        asked.append(list(languages))
        return [c for c in subs("de", EXACT) if c["language"] in languages]

    monkeypatch.setattr(auto, "translation_source_languages",
                        lambda meta, already=(): ["ar", "en"])
    monkeypatch.setattr(auto, "search_candidates", search)
    cache.delete_prefix("suboutlook")
    found = outlook.translation_candidates(META)
    assert [c["language"] for c in found] == ["de"]
    assert asked[0] == ["ar", "en"] and "de" in asked[1]

    del asked[:]
    cache.delete_prefix("suboutlook")
    monkeypatch.setattr(auto, "search_candidates",
                        lambda meta, languages: asked.append(list(languages))
                        or subs("en", EXACT))
    outlook.translation_candidates(META)
    assert asked == [["ar", "en"]], "no wider round when the usual ones answer"


def test_a_search_the_deadline_cut_short_is_not_remembered(monkeypatch):
    """In a real Kodi, Naruto Shippuden 3x55 lost its slow provider to the
    deadline and came back with nothing - and that nothing was the answer for
    the next hour: the second opening drew "0 candidates" without asking."""
    from pinky import cache
    asked = []
    cut = {"short": True}

    def search(meta, languages):
        asked.append(list(languages))
        return [] if cut["short"] else subs("he", EXACT)

    monkeypatch.setattr(auto, "search_candidates", search)
    monkeypatch.setattr(auto, "cut_short", lambda meta, languages: cut["short"])
    cache.delete_prefix("suboutlook")
    assert outlook.candidates(META) == []
    cut["short"] = False
    assert len(outlook.candidates(META)) == len(subs("he", EXACT))
    assert len(asked) == 2, "the cut-short answer was asked again, not recalled"
    outlook.candidates(META)
    assert len(asked) == 2, "and a whole answer is remembered"


def test_the_search_says_when_the_deadline_cut_it_short(monkeypatch):
    from pinky import http

    def run_parallel(tasks, workers=4, deadline=12.0, on_result=None, dropped=None):
        dropped.append("opensubtitles_rest")
        return {}

    monkeypatch.setattr(http, "run_parallel", run_parallel)
    monkeypatch.setattr(auto, "_providers", lambda: [("wizdom", None)])
    assert auto.search_candidates(META, ["he"]) == []
    assert auto.cut_short(META, ["he"]) is True
    assert auto.cut_short(META, ["en"]) is False


# --------------------------------------------------------------------------
# HEB DUB: the fourth rung, and the only one that needs no subtitle at all
# --------------------------------------------------------------------------

# Real names, taken from a live Torrentio survey of 128 films rather than
# invented, because the whole defect this rung fixes was a reading of names
# nobody had looked at: six of every nine spell it as one glued word.
REAL_DUBS = [
    "Aladdin.1992.1080p.BluRay.HebDub.x264",
    "The.Lion.King.1994.2160p.UHD.BluRay.HebDub.X265-iSrael",
    "The Lion King 1994 720p Hebrew Dubbed X264-LOL",
    "Frozen.2013.HDTV.HebDub.XviD-www.MORIDIM.TV.avi",
    "Toy Story 1 (1995) BDRip - HebDub XviD",
    "toy story 720p hebrew dubbed.mkv",
    "Shrek DVDRip. HebDub avi.",
]


@pytest.mark.parametrize("name", REAL_DUBS)
def test_a_real_hebrew_dub_reaches_the_dub_rung(found, name):
    rows = outlook.dub_rows(META, [source(name, 1, seeders=4)])
    assert [row["subs_mode"] for row in rows] == ["hebdub"]
    assert rows[0]["subs_fit"] == 100


def test_a_hebrew_subtitle_release_is_not_a_dub(found):
    """"HebSub" shares five of its six letters with "HebDub" and claims the
    opposite: Hebrew text over the original audio, not Hebrew speech."""
    assert outlook.dub_rows(
        META, [source("A.Film.2020.1080p.HebSub.x264", 1, seeders=9)]) == []


def test_a_release_naming_hebrew_and_a_dub_apart_is_offered_as_probable(found):
    """The real case no adjacency rule can see: a Walt Disney DVD image
    carrying both audio tracks, which says HEB, ENG and "dub" in three
    different places."""
    rows = outlook.dub_rows(META, [source(
        "[DVD-R.HEB.ENG].Aladdin.Walt.Disney-Full.DVD.dub.sub.iso", 1, seeders=2)])
    assert [row["subs_fit"] for row in rows] == [70]


def test_a_stated_dub_outranks_a_probable_one(found):
    rows = outlook.dub_rows(META, [
        source("[DVD-R.HEB.ENG].A.Film.DVD.dub.sub.iso", 1, seeders=2),
        source("A.Film.2020.1080p.HebDub.x264", 2, seeders=2)])
    assert [row["subs_fit"] for row in rows] == [100, 70]


def test_a_dead_torrent_is_not_offered_as_a_dub(found):
    """Of the 35 Hebrew dubs found over 128 films, 14 had no seeders at all.
    An uncached torrent nobody is seeding is not a film, it is a wait that
    never ends, and this rung must not promise Hebrew and deliver a spinner."""
    assert outlook.dub_rows(META, [
        source("A.Film.2020.1080p.HebDub.x264", 1, seeders=0, cached=False)]) == []


def test_a_cached_dub_needs_no_seeders(found):
    """The debrid service already holds the file, so the swarm behind it is
    irrelevant - and every dub found was an old release with a thin one."""
    rows = outlook.dub_rows(META, [
        source("A.Film.2020.1080p.HebDub.x264", 1, seeders=0, cached=True)])
    assert len(rows) == 1


def test_the_dub_rung_holds_five_at_most(found):
    many = [source("A.Film.%d.1080p.HebDub.x264" % i, i, seeders=2)
            for i in range(9)]
    assert len(outlook.dub_rows(META, many)) == outlook.DUB_ROWS == 5


def test_a_hebrew_film_gets_no_dub_rung(found):
    """Every release of an Israeli film is Hebrew audio, so listing them as
    "dubbed" says nothing - the same reason the other three rungs skip it."""
    hebrew = dict(META, original_language="he")
    assert outlook.dub_rows(
        hebrew, [source("Film.2020.1080p.HebDub.x264", 1, seeders=4)]) == []


def test_the_dub_rung_costs_no_provider_request(found, monkeypatch):
    """The point of asking it separately. The other three rungs each pay a
    subtitle search; this one reads a name already in hand, so it still
    answers when every provider found nothing."""
    def refuse(*args, **kwargs):
        raise AssertionError("the dub rung asked a provider")
    monkeypatch.setattr(outlook, "candidates", refuse)
    monkeypatch.setattr(outlook, "translation_candidates", refuse)
    monkeypatch.setattr(outlook, "english_candidates", refuse)
    rows = outlook.dub_rows(META, [source(REAL_DUBS[0], 1, seeders=4)])
    assert len(rows) == 1


def test_the_dub_rows_are_drawn_last(found):
    """A dub replaces the performance, so a subtitle somebody made over the
    original is the better answer where it exists. Last, not absent."""
    rows = [{"subs_mode": "hebdub", "subs_fit": 100},
            {"subs_mode": "native", "subs_fit": 70},
            {"subs_mode": "english", "subs_fit": 90},
            {"subs_mode": "llm", "subs_fit": 80}]
    assert [row["subs_mode"] for row in sources_window._in_one_order(rows)] == [
        "native", "llm", "english", "hebdub"]


def test_an_unscored_row_still_sorts_after_the_dub_rows():
    """`_ROUTE_ORDER` has a fourth entry now, so the default for a row with
    no mode had to move with it or the plain rows would interleave."""
    rows = [{"subs_mode": None, "subs_fit": 0},
            {"subs_mode": "hebdub", "subs_fit": 100}]
    assert [row["subs_mode"] for row in sources_window._in_one_order(rows)] == [
        "hebdub", None]


@pytest.mark.parametrize("name", [
    # The one that found the bug, live on Torrentio.
    "Monsters.Inc.DVDRip.heb.dub.HoRaDoT.NeT.avi",
    "A.Film.2020.heb-dub.x264",
    "A.Film.2020.Heb Dub.x264",
    "A.Film.2020.Hebrew.Dub.x264",
    "A.Film.2020.Dub.Hebrew.x264",
    "A.Film.2020.dub.heb.x264",
])
def test_a_bare_dub_is_read_as_one_not_only_dubbed(found, name):
    """`dubbed?` is "dubbe" plus an optional "d", so it never matched a plain
    "dub" - and a release saying "heb.dub" in so many words was read as
    having no dub at all. Every spelling in the wild, each one of them a real
    release-name shape, because this is a quantifier nobody reads twice."""
    rows = outlook.dub_rows(META, [source(name, 1, seeders=4)])
    assert [row["subs_fit"] for row in rows] == [100]
