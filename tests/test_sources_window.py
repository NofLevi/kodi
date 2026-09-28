"""The source picker.

This was the only window with no tests, and it was shipping a crash that made
it useless: building the badge for a cached source raised, and since cached
sources rank first, the first row took out the whole list. The viewer got a
title, no status, a blank button and nothing to choose from.

It survived because sources.autoplay is on by default, so the picker only
opens when someone deliberately asks to choose.
"""
import pytest

from pinky import kodi
from pinky.ui import sources_window


def source(title, **kwargs):
    # A hash per title. They all used to be "a" * 40, which is what three
    # copies of one torrent look like - and the picker now merges those.
    import hashlib
    entry = {"title": title,
             "hash": hashlib.sha1(title.encode("utf-8")).hexdigest(),
             "provider": "torrentio",
             "providers": ["torrentio"], "quality": "1080p",
             "size": 8 * 1024 ** 3, "seeders": 42, "languages": [],
             "hdr": [], "audio": "unknown", "cached": False, "cached_by": ""}
    entry.update(kwargs)
    return entry


CACHED = source("Film.2024.1080p.WEB-DL-GRP", cached=True, cached_by="torbox")
PLAIN = source("Film.2024.720p.WEB-DL-OTHER", quality="720p")


@pytest.fixture
def picker():
    window = sources_window.SourcesWindow()
    THIRD = source("Film.2024.2160p-THIRD")
    window.full = [CACHED, PLAIN, THIRD]
    # What pick_source assigns: the composed page, not the top-K. `_visible`
    # draws it verbatim, because the three subtitle lists are allowed to name
    # one release more than once and collapsing that is what hid the ENGLISH
    # rows behind the LLM ones.
    window.short = sources_window._plain([CACHED, PLAIN], window.full)
    window.meta = {"type": "movie", "title": "Film", "year": 2024}
    window.prepare()
    window.onInit()
    return window


# --------------------------------------------------------------------------
# the crash
# --------------------------------------------------------------------------


def test_a_cached_source_does_not_crash_the_badge():
    """The bug: .strip() bound to the tuple, not the formatted string."""
    badge = sources_window._badge(CACHED)
    assert badge, "a cached source must produce a badge, not raise"
    assert "1080P" in badge


def test_the_list_actually_fills(picker):
    control = picker.getControl(sources_window.LIST_SOURCES)
    assert control.size() == 3, "the picker rendered nothing at all before"


# --------------------------------------------------------------------------
# what the badge says about subtitles
# --------------------------------------------------------------------------


def _subs_line(entry):
    """The subtitle line a row would show.

    Its own property, because squeezed onto the badge the whole right-hand
    column was cut off. Read straight off the source, because the aggregator
    works it out before ranking - the badge and the order the rows are in
    come from the same numbers and cannot disagree.
    """
    row = dict(CACHED, subs_kind=entry["kind"], subs_score=entry["score"])
    return sources_window._list_item(row).getProperty("subs")


def test_a_release_carrying_hebrew_says_so():
    from pinky.subs import outlook

    line = _subs_line({"kind": outlook.EMBEDDED, "score": 0})
    assert kodi.localize(32474) in line
    assert "%" not in line, \
        "a claim from the release name does not get a percentage of ours"


def test_an_external_subtitle_shows_how_well_it_matches():
    from pinky.subs import outlook

    assert "82" in _subs_line({"kind": outlook.EXTERNAL, "score": 82})


def test_nothing_found_says_nothing_found():
    from pinky.subs import outlook

    assert kodi.localize(32476) in _subs_line({"kind": outlook.NONE,
                                               "score": 0})


def test_the_subtitle_line_is_separate_from_the_badge():
    """They are on different rows of the layout, and the badge must not grow
    to include the subtitle text - that is what cut it off."""
    from pinky.subs import outlook

    item = sources_window._list_item(
        dict(CACHED, subs_kind=outlook.EXTERNAL, subs_score=82))
    assert "82" not in item.getProperty("badge")
    assert "1080P" in item.getProperty("badge")
    assert "82" in item.getProperty("subs")


def test_a_name_match_is_labelled_as_an_estimate():
    """Before playback nothing has been downloaded or fitted.

    The number is how alike two filenames are, and measured against real
    timing that predicts little: subtitles whose names scored 70-89 fitted in
    none of three cases, while a 62 fitted at 0.95. It is a guess, and the
    picker now says so in the same word the subtitle chooser already used.
    """
    from pinky.subs import outlook

    line = _subs_line({"kind": outlook.EXTERNAL, "score": 82})
    assert "82" in line
    assert "estimate" in line.lower()


def test_a_source_nobody_looked_up_draws_without_a_subtitle_line():
    """The lookup can fail or be skipped, and a row must still draw."""
    assert sources_window._badge(CACHED)
    assert "1080P" in sources_window._badge(CACHED)
    assert sources_window._list_item(CACHED).getProperty("subs") == ""


def test_every_source_renders_even_the_awkward_ones():
    """One bad row used to abandon the whole list."""
    awkward = [
        source("No quality", quality=""),
        source("Unknown quality", quality="unknown"),
        source("No size at all", size=0, seeders=0),
        source("Hebrew", languages=["he"]),
        source("HDR", hdr=["hdr10", "dv"]),
        source("Cached elsewhere", cached=True, cached_by="realdebrid"),
        source("No providers", providers=[], provider=""),
    ]
    window = sources_window.SourcesWindow()
    window.short = awkward
    window.meta = {"title": "x"}
    window.prepare()
    window.onInit()
    assert window.getControl(sources_window.LIST_SOURCES).size() == len(awkward)


# --------------------------------------------------------------------------
# what the viewer reads
# --------------------------------------------------------------------------


def test_the_service_is_named_the_way_it_names_itself(picker):
    """"TorBox", not the module id shouted in capitals."""
    badge = sources_window._badge(CACHED)
    assert "TorBox" in badge
    assert "TORBOX" not in badge


def test_providers_are_named_not_module_ids():
    detail = sources_window._detail(
        source("x", providers=["torrentio", "mediafusion"]))
    assert "Torrentio" in detail and "MediaFusion" in detail
    assert "mediafusion" not in detail


def test_an_unknown_quality_is_not_called_sd():
    """Labelling an unknown release SD is a claim, not a fallback."""
    assert "SD" not in sources_window._badge(source("x", quality=""))
    assert "SD" not in sources_window._badge(source("x", quality="unknown"))


def test_a_known_quality_is_shown():
    assert "2160P" in sources_window._badge(source("x", quality="2160p"))


# --------------------------------------------------------------------------
# the window's own furniture
# --------------------------------------------------------------------------


def test_the_title_is_set_before_the_window_is_shown():
    """A control Kodi has not decided is visible cannot take focus, so these
    are set in prepare rather than onInit."""
    window = sources_window.SourcesWindow()
    window.short = [CACHED]
    window.meta = {"type": "movie", "title": "Film", "year": 2024}
    window.prepare()

    assert window.getProperty("pinky.sources.title") == "Film (2024)"
    assert window.getProperty("pinky.sources.status")


def test_an_episode_heading_says_which_episode():
    heading = sources_window._heading(
        {"type": "episode", "title": "Show", "season": 2, "episode": 5})
    assert "2x05" in heading






def test_choosing_a_row_returns_that_source(picker):
    control = picker.getControl(sources_window.LIST_SOURCES)
    control.selectItem(1)
    picker.onClick(sources_window.LIST_SOURCES)
    assert picker.chosen is PLAIN


def test_refresh_closes_and_asks_for_another_search(picker):
    picker.onClick(sources_window.BUTTON_REFRESH)
    assert picker.refresh_requested is True


def test_an_empty_list_still_says_something():
    window = sources_window.SourcesWindow()
    window.short = []
    window.meta = {"title": "Film", "year": 2024}
    window.prepare()
    window.onInit()
    assert window.getProperty("pinky.sources.status"), \
        "an empty picker must explain itself, not just be blank"


# --------------------------------------------------------------------------
# DUB, SUB and DUAL, which were parsed and never once drawn
# --------------------------------------------------------------------------

def test_the_dub_reaches_the_badge():
    """Anime publishes the same episode twice and the name is the only clue.

    `release.parse` has read this into a `dub` field all along and
    `model.label` has rendered it all along - but `model.label` has no callers,
    and the picker builds its own label, so it had never been on a screen. Two
    rows of the same resolution, size and group are indistinguishable without
    it, so choosing the dub meant starting one and backing out.
    """
    from pinky.ui import sources_window

    for value, expected in (("dub", "DUB"), ("sub", "SUB"), ("dual", "DUAL")):
        badge = sources_window._badge({"quality": "1080p", "dub": value})
        assert expected in badge, "%r is not in %r" % (expected, badge)


def test_no_dub_claim_adds_nothing():
    """It is blank for everything that is not anime, and that is deliberate.

    "MULTI.SUBS" on a live-action film is a claim about its subtitles and says
    nothing at all about the audio, so an empty field must stay invisible
    rather than becoming a third state on every row.
    """
    from pinky.ui import sources_window

    with_empty_dub = sources_window._badge(
        {"quality": "1080p", "dub": "", "cached": True})
    without_dub_at_all = sources_window._badge(
        {"quality": "1080p", "cached": True})
    for label in ("DUB", "SUB", "DUAL"):
        assert label not in with_empty_dub, with_empty_dub
    assert with_empty_dub == without_dub_at_all


# --------------------------------------------------------------------------
# saying which release carries its own subtitles
# --------------------------------------------------------------------------


def test_a_release_that_ships_subtitles_says_so(settings_module):
    """It is the best row on the page and looked like the seven either side.

    A subtitle typed against that exact cut is in time by construction - no
    hash, no correlation, no translation - and the picker had no way to say
    which one it was.
    """
    from pinky.ui import sources_window

    marked = sources_window._subtitle_badge(
        {"title": "A.Release-GRP", "subs_mode": "native", "subs_fit": 99,
         "bundled_subs": ["en"]})
    plain = sources_window._subtitle_badge(
        {"title": "A.Release-GRP", "subs_mode": "native", "subs_fit": 99})
    assert marked != plain
    assert "EN" in marked, "the language is the part that decides anything"


def test_a_row_nobody_could_ask_about_looks_as_it_did(settings_module):
    from pinky.ui import sources_window
    assert sources_window._bundled_mark({"bundled_subs": []}) == ""
    assert sources_window._bundled_mark({}) == ""


def test_the_mark_names_the_languages_you_read(settings_module):
    settings_module.set("subs.languages", "he,en")
    from pinky.ui import sources_window
    mark = sources_window._bundled_mark({"bundled_subs": ["he", "en"]})
    assert "HE" in mark and "EN" in mark


def test_a_language_nobody_here_reads_is_not_worth_a_mark(settings_module):
    """A YTS release of Toy Story 5 ships French and Portuguese.

    "FR/PT" beside a Hebrew percentage told a Hebrew household nothing,
    invited the two to be compared when they are about different subtitles,
    and was long enough to push the Hebrew figure off the end of the line -
    so the one number that decides anything read "Hebrew sub...".
    """
    settings_module.set("subs.languages", "he,en")
    from pinky.ui import sources_window
    assert sources_window._bundled_mark({"bundled_subs": ["fr", "pt"]}) == ""


def test_one_readable_language_among_several_still_earns_the_mark(settings_module):
    settings_module.set("subs.languages", "he,en")
    from pinky.ui import sources_window
    mark = sources_window._bundled_mark({"bundled_subs": ["fr", "he", "pt"]})
    assert "HE" in mark and "FR" not in mark


def test_rarbg_subs_folder_is_read_as_english():
    """`Subs/4_English.srt` and `Subs/5_English.srt` - two files, one fact."""
    from pinky.sources import bundled

    assert bundled._languages([
        {"name": "Top.Gun-RARBG/Subs/4_English.srt"},
        {"name": "Top.Gun-RARBG/Subs/5_English.srt"},
        {"name": "Top.Gun-RARBG/Top.Gun-RARBG.mp4"}]) == ["en"]


def test_an_unlabelled_file_is_not_given_a_language_it_may_not_have():
    """Probably English is not something to print beside a language somebody
    is deciding on."""
    from pinky.sources import bundled

    languages = bundled._languages([{"name": "pack/Some.Release.srt"}])
    assert languages == [bundled.UNLABELLED], "remembered as unlabelled"
    assert sources_window._bundled_mark({"bundled_subs": languages}) == "",         "and never printed as a language"


def test_only_cached_rows_are_asked_about(monkeypatch, no_network):
    """`_create` adds a torrent that is not there and uncached adds are capped
    at sixty an hour. Spending that to decorate a list nobody asked to act on
    would be the picker charging for being opened."""
    from pinky.sources import bundled

    bundled.forget()
    monkeypatch.setattr(bundled, "_may_look_inside", lambda source: False)
    asked = []
    monkeypatch.setattr(bundled, "_learn_later",
                        lambda rows: asked.extend(key for key, _r, _a in rows))

    bundled.annotate([{"hash": "a" * 40, "cached": False},
                      {"hash": "b" * 40, "cached": True}])
    assert asked == [bundled._key("b" * 40)]


def test_the_draw_never_waits_for_it(monkeypatch, no_network):
    """It decorates a list, and a decoration may not cost the list.

    Blocking for it spent its whole ceiling on every picker open - six
    seconds, then three - against a source search that already takes eleven.
    """
    from pinky.sources import bundled

    bundled.forget()
    monkeypatch.setattr(bundled, "_ask",
                        lambda source: (_ for _ in ()).throw(
                            AssertionError("asked on the drawing thread")))
    started = []
    monkeypatch.setattr(bundled, "_learn_later", lambda rows: started.append(rows))

    sources = [{"hash": "c" * 40, "cached": True}]
    assert bundled.annotate(sources) is sources
    assert started, "the asking happens, just not here"
    assert "bundled_subs" not in sources[0], "nothing is known yet, so nothing is claimed"


def test_what_is_known_is_applied_at_once(monkeypatch, no_network):
    """The second draw - "show all", or the next episode - is instant."""
    from pinky.sources import bundled

    bundled.forget()
    monkeypatch.setattr(bundled, "_may_look_inside", lambda source: False)
    bundled._remember("d" * 40, ["he", "en"])
    monkeypatch.setattr(bundled, "_learn_later",
                        lambda rows: (_ for _ in ()).throw(
                            AssertionError("asked about something already known")))

    sources = [{"hash": "d" * 40, "cached": True}]
    bundled.annotate(sources)
    assert sources[0]["bundled_subs"] == ["he", "en"]


def test_only_the_rows_on_screen_are_asked_about(monkeypatch, no_network):
    from pinky.sources import bundled

    bundled.forget()
    monkeypatch.setattr(bundled, "_may_look_inside", lambda source: False)
    asked = []
    monkeypatch.setattr(bundled, "_learn_later", lambda rows: asked.extend(rows))
    bundled.annotate([{"hash": "%040d" % n, "cached": True} for n in range(40)])
    assert len(asked) == bundled.MAX_ROWS


def test_the_background_pass_fills_what_the_draw_will_use(monkeypatch, no_network):
    from pinky.sources import bundled

    bundled.forget()
    bundled._learn_later([(bundled._key("e" * 40), bundled._remember_for("e" * 40),
                           lambda: ["he"])])
    import time
    for _ in range(60):
        if bundled._recall("e" * 40) is not None:
            break
        time.sleep(0.05)
    assert bundled._recall("e" * 40) == ["he"],         "written down, because the invocation that learned it is already gone"


def test_what_was_learned_outlives_the_invocation(monkeypatch, no_network):
    """The background pass runs inside a plugin invocation, and Kodi tears
    that down the moment the picker closes. Held in memory, everything it
    learned died with it and the mark never appeared at all."""
    from pinky.sources import bundled

    bundled.forget()
    monkeypatch.setattr(bundled, "_may_look_inside", lambda source: False)
    bundled._remember("f" * 40, ["he"])

    # A fresh dict would be empty here; the cache is not.
    sources = [{"hash": "f" * 40, "cached": True}]
    monkeypatch.setattr(bundled, "_learn_later",
                        lambda rows: (_ for _ in ()).throw(
                            AssertionError("asked about something written down")))
    bundled.annotate(sources)
    assert sources[0]["bundled_subs"] == ["he"]


def test_an_empty_answer_is_remembered_too(monkeypatch, no_network):
    """Otherwise every draw asks again about a release that carries nothing,
    which is most of them."""
    from pinky.sources import bundled

    bundled.forget()
    monkeypatch.setattr(bundled, "_may_look_inside", lambda source: False)
    bundled._remember("g" * 40, [])
    asked = []
    monkeypatch.setattr(bundled, "_learn_later", lambda rows: asked.extend(rows))
    bundled.annotate([{"hash": "g" * 40, "cached": True}])
    assert asked == []


def test_the_whole_list_is_on_screen_with_the_best_first(picker):
    """Reported twice as "show all shows the same thing", and fairly: the
    whole list begins with the rows already up. Measured on Toy Story 5, nine
    of the first ten were the same release in the same order. There is no
    toggle now - the good ones lead and the rest follow."""
    control = picker.getControl(sources_window.LIST_SOURCES)
    assert control.size() == 3, "the first page and everything it left out"
    assert control.getListItem(0).getLabel() == CACHED["title"], "best first"
    assert control.getListItem(2).getLabel() == "Film.2024.2160p-THIRD"


def test_a_release_on_the_first_page_is_not_listed_again(picker):
    labels = [picker.getControl(sources_window.LIST_SOURCES).getListItem(i).getLabel()
              for i in range(picker.getControl(sources_window.LIST_SOURCES).size())]
    assert len(labels) == len(set(labels))


def test_a_release_may_hold_more_than_one_row(picker):
    """NATIVE, LLM and ENGLISH are three answers to "how does this release get
    Hebrew", so a release with two of them earns two rows. Deduplicating here
    left one row per release: on Hikaru no Go every row read LLM while English
    subtitles fitting at 100% sat in a list that had been discarded."""
    same = dict(CACHED, subs_mode="llm", subs_fit=70)
    picker.short = [dict(CACHED, subs_mode="native", subs_fit=90), same]
    picker._render()

    assert picker.getControl(sources_window.LIST_SOURCES).size() == 2, \
        "one release, two routes, two rows"


def test_the_plain_list_still_shows_a_release_once(picker):
    """Where there is no comparison to draw there is nothing to repeat."""
    rows = sources_window._plain([CACHED], [CACHED, PLAIN])
    assert [r["title"] for r in rows] == [CACHED["title"], PLAIN["title"]]


def test_it_says_when_no_hebrew_subtitle_exists(monkeypatch):
    """An empty NATIVE list reads exactly like a failed search, and the two
    are different. Measured on The Odyssey (2026): OpenSubtitles holds 74
    subtitles for it - Arabic 7, Greek 7, Albanian 6 - and none in Hebrew."""
    from pinky import kodi
    from pinky.subs import outlook

    monkeypatch.setattr(outlook, "candidates", lambda meta, **k: [])
    note = sources_window._hebrew_note(
        [dict(CACHED, subs_mode="english")],
        {"type": "movie", "title": "The Odyssey", "year": 2026})
    assert note == kodi.localize(32553)


def test_it_says_when_hebrew_exists_but_fits_nothing(monkeypatch):
    """A different answer pointing at a different remedy: choose another
    release, or translate."""
    from pinky import kodi
    from pinky.subs import outlook

    monkeypatch.setattr(outlook, "candidates",
                        lambda meta, **k: [{"language": "he", "release": "Other"}])
    note = sources_window._hebrew_note(
        [dict(CACHED, subs_mode="llm")],
        {"type": "movie", "title": "A Film", "year": 2024})
    assert note == kodi.localize(32554)


def test_a_hebrew_title_is_not_told_it_has_no_hebrew(monkeypatch):
    """Its audio is Hebrew; the picker already says so and does not search."""
    from pinky.subs import outlook

    monkeypatch.setattr(outlook, "candidates", lambda meta, **k: [])
    assert sources_window._hebrew_note(
        [dict(CACHED, subs_mode="llm")],
        {"type": "movie", "title": "Fauda", "original_language": "he"}) == ""


def test_nothing_is_said_when_there_is_a_hebrew_row(monkeypatch):
    from pinky.subs import outlook

    monkeypatch.setattr(outlook, "candidates",
                        lambda meta, **k: pytest.fail("asked with a native row up"))
    assert sources_window._hebrew_note(
        [dict(CACHED, subs_mode="native", subs_fit=100)],
        {"type": "movie", "title": "Toy Story 5"}) == ""


def test_no_hebrew_is_not_a_dead_end_while_ai_can_translate(monkeypatch):
    """Measured over 65 titles: counting the best route rather than the Hebrew
    one, anime goes from an average of 31 to 98, and ten of its fifteen have
    no Hebrew subtitle in existence. "None exists" describes the corpus, not
    what the viewer is about to get."""
    from pinky import kodi
    from pinky.subs import outlook

    monkeypatch.setattr(outlook, "candidates", lambda meta, **k: [])
    note = sources_window._hebrew_note(
        [dict(CACHED, subs_mode="llm", subs_fit=100)],
        {"type": "episode", "title": "Attack on Titan", "year": 2013})
    assert note == kodi.localize(32555)


def test_hebrew_for_another_show_does_not_count_as_hebrew(monkeypatch):
    """On The Odyssey all twelve Hebrew candidates were for other shows -
    Doctor Odyssey, The Odyssey 1997, The Simpsons - and every one scored zero
    as the wrong title. The line then read "Hebrew subtitles exist but none
    fits these releases", which is a statement about the wrong twelve files."""
    from pinky import kodi
    from pinky.subs import outlook

    monkeypatch.setattr(outlook, "candidates", lambda meta, **k: [
        {"language": "he", "release": "Doctor.Odyssey.S01E18.1080p.WEB-DL"},
        {"language": "he", "release": "The.Simpsons.S01E03.1080p.DSNP.WEB-DL"},
    ])
    note = sources_window._hebrew_note(
        [dict(CACHED, subs_mode="llm", subs_fit=66)],
        {"type": "movie", "title": "The Odyssey", "year": 2026})

    assert note == kodi.localize(32555), \
        "candidates for other shows are not Hebrew for this film"


# --------------------------------------------------------------------------
# the subtitle tracks inside the file
# --------------------------------------------------------------------------


def _episode(info_hash, episode, **extra):
    source = {"hash": info_hash, "cached": True, "title": "Show - %02d.mkv" % episode,
              "extra": {"meta": {"type": "episode", "season": 1, "episode": episode}}}
    source.update(extra)
    return source


def test_what_is_inside_is_remembered_per_file_not_per_torrent(monkeypatch, no_network):
    """A season pack is one torrent and every episode in it is its own file."""
    from pinky.sources import bundled
    assert bundled._inside_key(_episode("h" * 40, 1)) != \
        bundled._inside_key(_episode("h" * 40, 2))


def test_the_full_tracks_are_read_and_the_partial_ones_are_not(monkeypatch, no_network):
    from pinky.debrid import registry
    from pinky.sources import bundled
    from pinky.subs import hasher
    from pinky.utils import matroska

    class Resolver(object):
        def resolve(self, source):
            return "https://cdn.example/file.mkv"

    monkeypatch.setattr(registry, "resolver_for", lambda source: Resolver())
    monkeypatch.setattr(hasher, "read_range", lambda url, start, end: b"x")
    monkeypatch.setattr(matroska, "subtitle_tracks", lambda data: [
        {"language": "en", "partial": False}, {"language": "en", "partial": True},
        {"language": "ar", "partial": False}])
    assert bundled._look_inside(_episode("i" * 40, 1)) == ["en", "ar"]


def test_a_file_that_cannot_be_read_is_not_asked_about_on_every_open(
        monkeypatch, no_network):
    from pinky.debrid import registry
    from pinky.sources import bundled

    bundled.forget()
    monkeypatch.setattr(registry, "resolver_for", lambda source: None)
    source = _episode("j" * 40, 3)
    answer = bundled._look_inside(source)
    bundled._remember_inside_for(source)(answer)
    assert bundled.recall_inside(source) == [], "known, and known to say nothing"


def test_the_inside_of_a_file_is_asked_only_for_the_top_rows_and_never_an_mp4(
        monkeypatch, no_network):
    from pinky.sources import bundled

    bundled.forget()
    monkeypatch.setattr(bundled, "_recall", lambda info_hash: [])
    asked = []
    monkeypatch.setattr(bundled, "_learn_later", lambda rows: asked.extend(rows))
    rows = [_episode("%040d" % n, 1) for n in range(10)]
    rows[0]["title"] = "Show - 01.mp4"
    bundled.annotate(rows)
    assert len(asked) == bundled.INSIDE_ROWS - 1


def test_the_background_pass_never_takes_the_whole_shared_pool(
        monkeypatch, no_network):
    """Playback resolves through the same four workers; a decoration holding
    all of them would make the next press wait on it."""
    from pinky import http
    from pinky.sources import bundled

    seen = {}

    def run_parallel(tasks, workers=4, deadline=12.0, **kwargs):
        seen["workers"] = workers
        return {}

    monkeypatch.setattr(http, "run_parallel", run_parallel)
    bundled._learn_later([("k", lambda languages: None, lambda: ["en"])])
    import time
    for _ in range(60):
        if "workers" in seen:
            break
        time.sleep(0.05)
    assert seen["workers"] <= 2


def test_a_track_inside_the_file_is_marked_like_a_file_beside_it():
    mark = sources_window._bundled_mark({"inside_subs": ["en"]})
    assert "EN" in mark
