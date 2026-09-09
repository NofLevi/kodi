"""The whole subtitle decision, from candidates to a file on disk."""
import os

import pytest

from katan.subs import auto, srt


MOVIE = {
    "type": "movie",
    "ids": {"imdb": "tt15239678", "tmdb": 693134},
    "title": "Dune: Part Two",
    "year": 2024,
    "stream_url": "",
    "source": {"release": "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX",
               "group": "flux", "quality": "1080p"},
}


def srt_bytes(count=30, offset=0.0, text="hello"):
    cues = [srt.Cue(i + 1, offset + i * 4.0, offset + i * 4.0 + 2.5,
                    "%s %d" % (text, i)) for i in range(count)]
    return srt.dump(cues).encode("utf-8")


@pytest.fixture
def pipeline(monkeypatch, settings_module):
    """Replace the providers with a controllable fake."""
    settings_module.set_many({
        "subs.auto": "true",
        "subs.languages": "he,en",
        "subs.threshold": "70",
        "subs.hash_match": "false",
        "subs.ai.enabled": "false",
        "subs.provider.wizdom": "true",
        "subs.provider.opensubtitles": "false",
        "subs.provider.subsource": "false",
    })

    state = {"candidates": [], "downloads": {}, "downloaded": [],
             "expected": []}

    def fake_search(meta, languages, video_hash=""):
        return list(state["candidates"])

    def fake_download(candidate, expect_language=None):
        state["downloaded"].append(candidate.get("release"))
        state["expected"].append(expect_language)
        key = candidate.get("download") or candidate.get("release")
        data = state["downloads"].get(key) or state["downloads"].get(
            candidate.get("release"), b"")
        cues = srt.parse(srt.decode(data)) if data else []
        return srt.clean(cues) if cues else []

    monkeypatch.setattr(auto, "search_candidates", fake_search)
    monkeypatch.setattr(auto, "download_candidate", fake_download)
    return state


def candidate(release, language="he", **kwargs):
    entry = {"provider": "wizdom", "language": language, "release": release,
             "download": release}
    entry.update(kwargs)
    return entry


def test_a_matching_hebrew_subtitle_is_downloaded_and_stored(pipeline):
    name = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [candidate(name)]
    pipeline["downloads"][name] = srt_bytes()

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert path and os.path.isfile(path)
    assert path.endswith(".he.srt")
    assert report["translated"] is False
    assert srt.read(path), "the stored file should be readable SRT"


def test_only_one_subtitle_is_ever_downloaded(pipeline):
    """The point of scoring first is to not fetch a dozen files."""
    good = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [
        candidate("Dune.Part.Two.2024.720p.HDTV-AAA"),
        candidate(good),
        candidate("Dune.Part.Two.2024.DVDRip-BBB"),
    ]
    pipeline["downloads"][good] = srt_bytes()

    auto.find_and_prepare(MOVIE, ["he", "en"])
    assert pipeline["downloaded"] == [good]


def test_no_candidates_is_reported_not_crashed(pipeline):
    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert path == ""
    assert report["reason"] == "no candidates"


def test_a_weak_match_is_still_used_as_a_last_resort(pipeline):
    weak = "Some.Unrelated.Release.2019.DVDRip-XYZ"
    pipeline["candidates"] = [candidate(weak)]
    pipeline["downloads"][weak] = srt_bytes()

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert path, "an imperfect subtitle beats none"
    assert report["reason"] == "below threshold, used anyway"


def test_english_is_translated_when_no_hebrew_is_good_enough(pipeline, monkeypatch,
                                                            settings_module):
    settings_module.set("subs.ai.enabled", "true")
    english = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [candidate(english, language="en")]
    pipeline["downloads"][english] = srt_bytes(count=12, text="english")

    from katan.subs.ai import translator

    def fake_translate(cues, language, on_progress=None, meta=None):
        return [srt.Cue(c.index, c.start, c.end, "HE " + c.text) for c in cues]

    monkeypatch.setattr(translator, "available", lambda: True)
    monkeypatch.setattr(translator, "translate", fake_translate)

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert report["translated"] is True
    assert report["source_language"] == "en"

    cues = srt.read(path)
    assert cues[0].text.startswith("HE ")
    assert cues[0].start == 0.0, "translation must not move timings"


def test_a_hash_matched_reference_re_times_the_chosen_subtitle(pipeline):
    """The Hebrew file is 8 seconds out; the English hash match proves it."""
    hebrew = "Dune.Part.Two.2024.1080p.BluRay-OTHER"
    english = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [
        candidate(hebrew, language="he", sync_percent=100),
        candidate(english, language="en", moviehash="abc", hash_match=True),
    ]
    pipeline["downloads"][hebrew] = srt_bytes(count=60, offset=8.0)
    pipeline["downloads"][english] = srt_bytes(count=60, offset=0.0)

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert path
    assert report["synchronised"] is True, "the offset should have been corrected"

    cues = srt.read(path)
    assert abs(cues[0].start - 0.0) < 0.3, "expected the 8 second shift removed"


def test_cached_subtitles_are_reused_without_searching(pipeline):
    name = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [candidate(name)]
    pipeline["downloads"][name] = srt_bytes()

    first, _ = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert auto.cached_subtitle(MOVIE, "he") == first

    pipeline["downloaded"] = []
    assert auto.cached_subtitle(MOVIE, "he"), "a second play should hit the cache"
    assert pipeline["downloaded"] == []


def test_the_subtitle_folder_is_capped(settings_module):
    settings_module.set("subs.cache_files", "10")
    directory = auto.subtitle_dir()
    for index in range(25):
        with open(os.path.join(directory, "f%02d.srt" % index), "w") as handle:
            handle.write("1\n00:00:01,000 --> 00:00:02,000\nx\n\n")
    auto.prune_cache()
    remaining = [n for n in os.listdir(directory) if n.endswith(".srt")]
    assert len(remaining) == 10


def test_episode_and_movie_names_do_not_collide():
    movie = auto.name_for(MOVIE, "he")
    episode = auto.name_for({"type": "episode", "ids": {"imdb": "tt0903747"},
                             "season": 2, "episode": 7}, "he")
    assert movie != episode
    assert "s02e07" in episode


def test_a_partial_translation_reaches_the_player_while_it_runs(pipeline,
                                                                monkeypatch,
                                                                settings_module):
    """The viewer should see subtitles from the first chunk, not at the end."""
    settings_module.set("subs.ai.enabled", "true")
    english = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [candidate(english, language="en")]
    pipeline["downloads"][english] = srt_bytes(count=24, text="english")

    from katan.subs.ai import translator

    def fake_translate(cues, language, on_progress=None, meta=None):
        translated = [srt.Cue(c.index, c.start, c.end, "HE " + c.text)
                      for c in cues]
        if on_progress:
            on_progress(12, 24, translated[:12] + cues[12:])
            on_progress(24, 24, translated)
        return translated

    monkeypatch.setattr(translator, "available", lambda: True)
    monkeypatch.setattr(translator, "translate", fake_translate)

    shown = []

    class FakePlayer(object):
        def setSubtitles(self, path):
            shown.append((path, os.path.getsize(path)))

        def showSubtitles(self, visible):
            pass

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"],
                                         player=FakePlayer())

    assert report["translated"] is True
    assert shown, "nothing was put on screen before the end"
    assert all(size > 0 for _p, size in shown)

    # Kodi caches a subtitle by path, so consecutive writes must alternate.
    if len(shown) > 1:
        assert shown[0][0] != shown[1][0], \
            "the same path twice would not refresh on screen"

    assert os.path.isfile(path)
    for partial, _size in shown:
        assert not os.path.isfile(partial), "partial files should be cleaned up"


def test_a_gender_marking_language_is_preferred_as_the_translation_source(
        pipeline, monkeypatch, settings_module):
    """Spanish carries the speaker's gender into Hebrew; English cannot."""
    settings_module.set_many({"subs.ai.enabled": "true",
                              "subs.languages": "he,en,es"})
    # Identical release names, so the only thing separating them is the
    # language. A better-matching English subtitle would rightly still win.
    release = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [
        dict(candidate(release, language="en"), download="english"),
        dict(candidate(release, language="es"), download="spanish"),
    ]
    pipeline["downloads"]["english"] = srt_bytes(text="english")
    pipeline["downloads"]["spanish"] = srt_bytes(text="spanish")

    from katan.subs.ai import translator
    monkeypatch.setattr(translator, "available", lambda: True)
    monkeypatch.setattr(translator, "translate",
                        lambda cues, language, on_progress=None, meta=None: cues)

    _path, report = auto.find_and_prepare(MOVIE, ["he", "en", "es"])
    assert report["source_language"] == "es"


def test_the_automatic_path_prefers_an_embedded_track(monkeypatch,
                                                      settings_module):
    """The best case costs nothing: the file already carries the subtitle."""
    import xbmc
    from katan.subs import auto, embedded

    settings_module.set_many({"subs.auto": "true", "subs.languages": "he,en",
                              "subs.embedded_first": "true"})
    xbmc.JSONRPC_RESULTS["Player.GetProperties"] = {"subtitles": [
        {"index": 0, "language": "eng", "name": "English"},
        {"index": 1, "language": "heb", "name": "Hebrew"},
    ]}

    chosen = []
    monkeypatch.setattr(embedded, "select",
                        lambda index: chosen.append(index) or True)

    searched = []
    monkeypatch.setattr(auto, "search_candidates",
                        lambda *a, **k: searched.append(1) or [])

    class FakePlayer(object):
        def setSubtitles(self, path):
            raise AssertionError("no file should be needed")

        def showSubtitles(self, visible):
            pass

    try:
        auto.on_playback_started(FakePlayer(), MOVIE)
    finally:
        xbmc.JSONRPC_RESULTS.clear()

    assert chosen == [1], "the Hebrew track should have been selected"
    assert not searched, "no provider should be contacted when one is embedded"


def test_a_forced_embedded_track_is_not_used_automatically(monkeypatch,
                                                           settings_module):
    """Forced tracks caption signs, not dialogue, so they are not a subtitle."""
    import xbmc
    from katan.subs import auto, embedded

    settings_module.set_many({"subs.auto": "true", "subs.languages": "he,en"})
    xbmc.JSONRPC_RESULTS["Player.GetProperties"] = {"subtitles": [
        {"index": 0, "language": "heb", "name": "Hebrew (Forced)"},
    ]}
    monkeypatch.setattr(embedded, "select",
                        lambda index: (_ for _ in ()).throw(
                            AssertionError("forced track was selected")))
    try:
        assert auto.use_embedded(None, "he") is False
    finally:
        xbmc.JSONRPC_RESULTS.clear()


# --------------------------------------------------------------------------
# a subtitle that is not in the language it claims
# --------------------------------------------------------------------------


def test_the_automatic_path_asks_for_the_language_to_be_checked(pipeline):
    """A subtitle listed as Hebrew and written in English is a mislabelled
    upload, not a rarity, and applying it silently gives the viewer the wrong
    language with no clue why."""
    name = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [candidate(name)]
    pipeline["downloads"][name] = srt_bytes()

    auto.find_and_prepare(MOVIE, ["he", "en"])
    assert pipeline["expected"], "something should have been downloaded"
    assert pipeline["expected"][0] == "he"


def test_the_chooser_does_not_second_guess_the_viewer(monkeypatch):
    """They picked that entry. Refusing it would be worse than honouring a
    bad choice they can see and change."""
    from katan.subs import service

    seen = []
    monkeypatch.setattr(auto, "download_candidate",
                        lambda cand, expect_language=None:
                        seen.append(expect_language) or [])
    monkeypatch.setattr(service, "_current_meta", lambda: dict(MOVIE))
    service.dispatch(["plugin://plugin.video.katan/", "1",
                      "?action=download&provider=wizdom&id=x&language=he"])
    assert seen == [None]


def test_an_english_file_labelled_hebrew_is_refused(monkeypatch,
                                                    settings_module):
    """The whole point of the check, at the level it actually runs."""
    from katan.subs import auto as real

    english = (b"1\r\n00:00:01,000 --> 00:00:03,000\r\n"
               b"Hello there, how are you today?\r\n\r\n")
    monkeypatch.setattr(real, "_modules",
                        lambda: {"wizdom": type("M", (), {
                            "download": staticmethod(lambda c: english)})})

    entry = {"provider": "wizdom", "language": "he", "release": "x",
             "download": "x"}
    assert real.download_candidate(entry) != [], \
        "without a language to check it is taken as given"
    assert real.download_candidate(entry, expect_language="he") == []


def test_the_same_subtitle_arriving_twice_is_counted_once(monkeypatch,
                                                          settings_module):
    """Asking by hash and asking by title are different questions with
    overlapping answers.

    A duplicate costs more than it looks: `outlook` weighs every candidate
    against every source when the picker opens - 240 sources against 32
    candidates on a real search - so one extra candidate is one extra
    comparison per source, on a projector with a gigabyte of RAM.
    """
    from katan.subs import auto

    same = {"provider": "opensubtitles_rest", "language": "he",
            "release": "Silo.S01E01.1080p-PSA", "download": "https://dl/1"}
    other = {"provider": "wizdom", "language": "he", "release": "other",
             "download": "https://dl/2"}

    class Fake(object):
        @staticmethod
        def search(*args, **kwargs):
            return [dict(same), dict(same), dict(other)]

    monkeypatch.setattr(auto, "_providers", lambda: [("wizdom", Fake)])
    found = auto.search_candidates({"type": "movie", "ids": {}}, ["he"])
    links = [c["download"] for c in found]
    assert links == ["https://dl/1", "https://dl/2"], links


def test_a_candidate_with_no_link_is_still_kept(monkeypatch, settings_module):
    """Deduping on a missing key would collapse them all into one."""
    from katan.subs import auto

    rows = [{"provider": "x", "language": "he", "release": "a", "download": ""},
            {"provider": "x", "language": "he", "release": "b", "download": ""}]

    class Fake(object):
        @staticmethod
        def search(*args, **kwargs):
            return [dict(r) for r in rows]

    monkeypatch.setattr(auto, "_providers", lambda: [("wizdom", Fake)])
    found = auto.search_candidates({"type": "movie", "ids": {}}, ["he"])
    assert len(found) == 2
