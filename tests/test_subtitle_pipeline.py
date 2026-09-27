"""The whole subtitle decision, from candidates to a file on disk."""
import os

import pytest

from pinky.subs import auto, srt


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
             "expected": [], "searched_languages": []}

    def fake_search(meta, languages, video_hash="", **kwargs):
        state["searched_languages"].append(list(languages))
        return list(state["candidates"])

    def fake_download(candidate, expect_language=None, outcome=None):
        state["downloaded"].append(candidate.get("release"))
        state["expected"].append(expect_language)
        key = candidate.get("download") or candidate.get("release")
        data = state["downloads"].get(key) or state["downloads"].get(
            candidate.get("release"), b"")
        if outcome is not None:
            outcome["served"] = bool(data)
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



def test_an_unusable_top_match_falls_back_to_the_next_candidate(pipeline):
    """A broken 100% upload must not hide a usable lower-ranked subtitle."""
    exact = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    fallback = "Dune.Part.Two.2024.1080p.WEB-DL.H264-OTHER"
    pipeline["candidates"] = [candidate(exact), candidate(fallback)]
    pipeline["downloads"][exact] = b"not a subtitle"
    pipeline["downloads"][fallback] = srt_bytes()

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert path and os.path.isfile(path)
    assert pipeline["downloaded"] == [exact, fallback]
    assert report["reason"]



def test_a_weak_hebrew_subtitle_comes_before_a_translation(
        pipeline, monkeypatch, settings_module):
    """Strictly Hebrew, then AI, then English: a Hebrew subtitle somebody made
    for this title, even one below the threshold, comes ahead of one a model
    makes. It used to be the other way round."""
    settings_module.set("subs.ai.enabled", "true")
    exact = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    weak = "Some.Unrelated.Release.2019.DVDRip-XYZ"
    english = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX.en"
    pipeline["candidates"] = [candidate(exact), candidate(weak),
                              candidate(english, language="en")]
    pipeline["downloads"][exact] = b"broken"
    pipeline["downloads"][weak] = srt_bytes(text="weak")
    pipeline["downloads"][english] = srt_bytes(text="english")

    from pinky.subs.ai import translator
    monkeypatch.setattr(translator, "available", lambda: True)
    monkeypatch.setattr(translator, "translate", lambda *a, **k: pytest.fail(
        "translated while a Hebrew subtitle existed"))

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert path.endswith(".he.srt") and report["translated"] is False
    assert report["reason"] == "below threshold, used anyway"
    assert pipeline["searched_languages"] == [["he", "en"]],         "no AI sources are searched while Hebrew exists"


def test_broken_candidates_are_walked_past_but_not_forever(pipeline):
    """Unusable files cost attempts rather than budget slots, up to a ceiling.

    These arrive as bytes that will not parse, which is the upload's fault
    rather than the provider's, so the provider is not written off - only the
    overall attempt ceiling stops the walk.
    """
    names = ["Dune.Part.Two.2024.1080p.WEB-DL.H264-%s" % group
             for group in ("FLUX", "AAA", "BBB", "CCC", "DDD", "EEE",
                           "FFF", "GGG", "HHH", "III", "JJJ", "KKK")]
    pipeline["candidates"] = [candidate(name) for name in names]
    for name in names:
        pipeline["downloads"][name] = b"broken"

    path, _report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert path == ""
    assert pipeline["downloaded"] == names[:auto._DownloadBudget.MAX_FAILURES], \
        "a download that returns nothing costs an attempt, not a budget slot"


def test_a_half_length_subtitle_is_replaced_by_a_whole_one(pipeline):
    """A CD1 that ends half way through must not end the search.

    `verify_and_sync` has always claimed "the caller's fallback to the next
    candidate handles both". For the Hebrew step it did not exist, so one
    partial file meant no subtitle at all: Harry Potter 2 had fourteen Hebrew
    subtitles, the best-ranked was a 710-cue half, and the film played with
    none.
    """
    half = "Harry.Potter.2002.1080p.BluRay.x264-CD1"
    whole = "Harry.Potter.2002.1080p.BluRay.x264-DOMiNiON"
    pipeline["candidates"] = [candidate(half), candidate(whole)]
    # 30 cues four seconds apart reach 2 minutes; the film is an hour.
    pipeline["downloads"][half] = srt_bytes(count=30, text="half")
    pipeline["downloads"][whole] = srt_bytes(count=900, text="whole")
    MOVIE["duration"] = 3600

    try:
        path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    finally:
        MOVIE.pop("duration", None)

    assert path.endswith(".he.srt")
    assert srt.read(path)[-1].text.startswith("whole"), \
        "the partial subtitle should have been passed over"


def test_a_dead_provider_does_not_hide_a_working_one(pipeline):
    """The budget is three files to parse, not three requests to make.

    Measured over 223 titles: when OpenSubtitles started refusing downloads
    mid-run, its three empty fetches spent the whole budget and Pulp Fiction
    played with nothing while 45 working Wizdom candidates sat behind them.
    """
    dead = ["Dune.Part.Two.2024.1080p.WEB-DL.H264-%s" % group
            for group in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF",
                          "GGG", "HHH", "III", "JJJ", "KKK")]
    alive = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [candidate(name, provider="opensubtitles_rest")
                              for name in dead]
    pipeline["candidates"].append(candidate(alive, provider="wizdom"))
    # A throttled service hands over no bytes at all, which is how it is told
    # apart from an upload that arrives and will not parse.
    for name in dead:
        pipeline["downloads"][name] = b""
    pipeline["downloads"][alive] = srt_bytes(text="hebrew")

    path, _report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert path.endswith(".he.srt")
    assert alive in pipeline["downloaded"]
    assert len(pipeline["downloaded"]) <= auto._DownloadBudget.PROVIDER_FAILURES + 1, \
        "the sick provider should be written off after two empty answers"



def test_hash_reference_and_target_fallback_share_three_download_budget(
        pipeline, monkeypatch):
    exact = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    second = "Dune.Part.Two.2024.1080p.WEB-DL.H264-AAA"
    usable = "Dune.Part.Two.2024.1080p.WEB-DL.H264-BBB"
    reference = "Dune.Part.Two.2024.1080p.WEB-DL.H264-ENG"
    pipeline["candidates"] = [
        candidate(exact), candidate(second), candidate(usable),
        candidate(reference, language="en", moviehash="abcd")]
    pipeline["downloads"].update({
        exact: b"broken", second: b"broken",
        usable: srt_bytes(text="hebrew"),
        reference: srt_bytes(text="english"),
    })
    monkeypatch.setattr(auto, "video_hash_later", lambda meta: lambda: "abcd")

    path, _report = auto.find_and_prepare(MOVIE, ["he", "en"])

    assert path, "the third target candidate should consume the last slot"
    # The two broken fetches cost attempts rather than slots, so the hash
    # reference is still affordable - which matters more than it looks: it is
    # the only thing that can confirm the chosen subtitle's timing, and under
    # the old counting a pair of dead downloads made it unreachable.
    assert pipeline["downloaded"] == [exact, second, usable, reference]


def test_download_budget_deduplicates_the_provider_handle(monkeypatch):
    calls = []
    cues = [srt.Cue(1, 0.0, 1.0, "line")]
    monkeypatch.setattr(
        auto, "download_candidate",
        lambda candidate, expect_language=None, outcome=None:
            calls.append(candidate) or cues)
    budget = auto._DownloadBudget(3)
    left = candidate("release-a", download="same-handle")
    right = candidate("release-b", download="same-handle")

    assert budget.fetch(left) is cues
    assert budget.fetch(right) is cues
    assert len(calls) == 1


def test_download_budget_keeps_languages_separate_in_one_archive(monkeypatch):
    calls = []

    def download(candidate, expect_language=None, outcome=None):
        language = expect_language or candidate.get("language")
        calls.append(language)
        return [srt.Cue(1, 0.0, 1.0, language)]

    monkeypatch.setattr(auto, "download_candidate", download)
    budget = auto._DownloadBudget(3)
    english = candidate("release", language="en", download="same-archive")
    spanish = candidate("release", language="es", download="same-archive")

    assert budget.fetch(english)[0].text == "en"
    assert budget.fetch(spanish)[0].text == "es"
    assert calls == ["en", "es"]


def test_consensus_failure_tries_another_accepted_target_before_translation(
        pipeline, monkeypatch, settings_module):
    """Foreign evidence must not hide a usable accepted Hebrew candidate."""
    settings_module.set("subs.ai.enabled", "true")
    broken = "Dune.Part.Two.2024.1080p.WEB-DL.H264-AAA"
    usable = "Dune.Part.Two.2024.1080p.WEB-DL.H264-BBB"
    english = "Dune.Part.Two.2024.1080p.WEB-DL.H264-ENG"
    spanish = "Dune.Part.Two.2024.1080p.WEB-DL.H264-SPA"
    pipeline["candidates"] = [
        candidate(broken), candidate(usable),
        candidate(english, language="en", provider="open", uploader="alice"),
        candidate(spanish, language="es", provider="open", uploader="bob"),
    ]
    pipeline["downloads"].update({
        broken: b"broken", usable: srt_bytes(text="hebrew"),
        english: srt_bytes(text="english"), spanish: srt_bytes(text="spanish"),
    })

    from pinky.subs.ai import translator
    monkeypatch.setattr(translator, "available", lambda: True)
    monkeypatch.setattr(
        translator, "translate",
        lambda cues, language, on_progress=None, meta=None, **kwargs:
        pytest.fail("accepted Hebrew fallback should win before translation"))

    path, report = auto.find_and_prepare(MOVIE, ["he", "en", "es"])
    assert path and report["translated"] is False
    assert usable in pipeline["downloaded"]


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

    from pinky.subs.ai import translator

    def fake_translate(cues, language, on_progress=None, meta=None, **kwargs):
        return [srt.Cue(c.index, c.start, c.end, "HE " + c.text) for c in cues]

    monkeypatch.setattr(translator, "available", lambda: True)
    monkeypatch.setattr(translator, "translate", fake_translate)

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert report["translated"] is True
    assert report["source_language"] == "en"

    cues = srt.read(path)
    assert cues[0].text.startswith("HE ")
    assert cues[0].start == 0.0, "translation must not move timings"




def test_automatic_search_keeps_primary_language_request_bounded(pipeline):
    """Optional timing evidence must not delay or discard the primary search."""
    auto.find_and_prepare(MOVIE, ["he", "en"])
    assert pipeline["searched_languages"] == [["he", "en"]]



def test_timing_evidence_asks_enough_languages_to_prove_a_timeline(
        monkeypatch, settings_module):
    """Two agreeing non-target languages, or the proof cannot be made.

    It used to ask for `missing[:1]` out of ("en", "es"), which with
    subs.languages he,en is exactly one language - Spanish. Cross-language
    verification needs *two* independent timelines to compare, so it could
    never run: one short by construction.

    Still bounded, which is the other half of the contract - it cannot become
    six serial REST requests behind one deadline - and none of them carries
    the hash, because the primary search already used it.
    """
    from pinky.subs import auto
    from pinky.subs.providers import opensubtitles_rest

    calls = []

    def search(meta, target, languages, video_hash="", video_size=0):
        calls.append((list(languages), video_hash, video_size))
        return []

    monkeypatch.setattr(opensubtitles_rest, "search", search)
    auto.search_timing_evidence(MOVIE, ["he", "en"], "file-hash")

    asked = [languages[0] for languages, _hash, _size in calls]
    assert len(asked) == auto.EVIDENCE_LANGUAGES_ASKED
    assert len(asked) >= 2, "one language can never agree with another"
    assert "en" not in asked and "he" not in asked, "already searched"
    assert all(video_hash == "" for _l, video_hash, _s in calls)
    assert asked[0] == "ar", "widest coverage first"


def test_timing_evidence_is_skipped_when_it_has_nothing_to_add(
        monkeypatch, settings_module):
    """A viewer who already reads all of them needs no extra request."""
    from pinky.subs import auto
    from pinky.subs.providers import opensubtitles_rest

    monkeypatch.setattr(opensubtitles_rest, "search",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    assert auto.search_timing_evidence(
        MOVIE, ["he"] + list(auto.TIMING_EVIDENCE_LANGUAGES), "") == []


def test_two_other_languages_verify_and_retime_the_requested_subtitle(
        pipeline, settings_module, monkeypatch):
    """Three tiny subtitle files replace expensive audio analysis."""
    settings_module.set("subs.languages", "he,en,es")
    hebrew = "Dune.Part.Two.2024.1080p.WEB-DL.H264-HEB"
    english = "Dune.Part.Two.2024.1080p.WEB-DL.H264-ENG"
    spanish = "Dune.Part.Two.2024.1080p.WEB-DL.H264-SPA"
    pipeline["candidates"] = [
        candidate(hebrew, language="he", provider="wizdom"),
        candidate(english, language="en", provider="opensubtitles_rest",
                  uploader="alice"),
    ]
    monkeypatch.setattr(
        auto, "search_timing_evidence",
        lambda meta, languages, video_hash="": [
            candidate(spanish, language="es", provider="opensubtitles_rest",
                      uploader="bob")],
        raising=False)
    pipeline["downloads"][hebrew] = srt_bytes(count=80, offset=90.0,
                                               text="hebrew")
    pipeline["downloads"][english] = srt_bytes(count=80, offset=0.0,
                                                text="english")
    pipeline["downloads"][spanish] = srt_bytes(count=80, offset=0.2,
                                                text="spanish")

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])

    assert report["synchronised"] is True
    assert report["timing_evidence"] == "cross-language consensus"
    assert abs(srt.read(path)[0].start) < 0.3
    assert len(pipeline["downloaded"]) == 3


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


def test_hash_reference_rejects_an_unrelated_target_and_tries_next(
        pipeline):
    wrong = "Dune.Part.Two.2024.1080p.WEB-DL.H264-WRONG"
    right = "Dune.Part.Two.2024.1080p.WEB-DL.H264-RIGHT"
    reference = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX.en"
    pipeline["candidates"] = [
        candidate(wrong, sync_percent=100),
        candidate(right, sync_percent=90),
        candidate(reference, language="en", moviehash="abc", hash_match=True),
    ]
    import random
    from pinky.subs import sync
    rng = random.Random(99)
    unrelated = []
    position = 0.0
    for i in range(200):
        position += rng.uniform(1.0, 11.0)
        unrelated.append(srt.Cue(i + 1, position, position + rng.uniform(0.3, 1.4),
                                 "wrong %d" % i))
    reference_cues = srt.parse(srt.decode(srt_bytes(count=200, offset=0.0,
                                                     text="english")))
    assert sync.fit(unrelated, reference_cues)[2] < sync.MIN_CONFIDENCE
    pipeline["downloads"][wrong] = srt.dump(unrelated).encode("utf-8")
    pipeline["downloads"][right] = srt_bytes(count=200, offset=4.0,
                                              text="right")
    pipeline["downloads"][reference] = srt_bytes(count=200, offset=0.0,
                                                  text="english")

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])

    assert path
    assert srt.read(path)[0].text.startswith("right")
    assert pipeline["downloaded"] == [wrong, reference, right]
    assert report["synchronised"] is True


def test_cached_subtitles_are_reused_without_searching(pipeline):
    name = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [candidate(name)]
    pipeline["downloads"][name] = srt_bytes()

    first, _ = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert auto.cached_subtitle(MOVIE, "he") == first

    pipeline["downloaded"] = []
    assert auto.cached_subtitle(MOVIE, "he"), "a second play should hit the cache"
    assert pipeline["downloaded"] == []


def test_different_video_releases_do_not_share_a_cached_subtitle():
    first = dict(MOVIE, source={"file_name": "Film.2024.WEB-DL-A.mkv",
                                "file_size": 1000})
    second = dict(MOVIE, source={"file_name": "Film.2024.BluRay-B.mkv",
                                 "file_size": 2000})
    assert auto.name_for(first, "he") != auto.name_for(second, "he")


def test_a_corrupt_cached_subtitle_is_not_reused(settings_module):
    path = os.path.join(auto.subtitle_dir(), auto.name_for(MOVIE, "he"))
    with open(path, "wb") as handle:
        handle.write(b"not a subtitle")
    assert auto.cached_subtitle(MOVIE, "he") == ""


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


def test_failed_partial_translation_is_removed_from_player(
        monkeypatch, settings_module):
    from pinky.subs.ai import translator

    settings_module.set("subs.ai.enabled", "true")
    cues = srt.parse(srt.decode(srt_bytes(count=4)))
    visibility = []

    class Player(object):
        def setSubtitles(self, path):
            pass

        def showSubtitles(self, visible):
            visibility.append(visible)

    def fail(cues, language, on_progress=None, **kwargs):
        on_progress(2, 4, cues)
        raise translator.TranslationError("partial")

    monkeypatch.setattr(translator, "translate", fail)
    assert auto._translate_progressively(cues, MOVIE, "he", Player()) == []
    assert visibility[-1] is False


def test_a_partial_translation_reaches_the_player_while_it_runs(pipeline,
                                                                monkeypatch,
                                                                settings_module):
    """The viewer should see subtitles from the first chunk, not at the end."""
    settings_module.set("subs.ai.enabled", "true")
    english = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [candidate(english, language="en")]
    pipeline["downloads"][english] = srt_bytes(count=24, text="english")

    from pinky.subs.ai import translator

    def fake_translate(cues, language, on_progress=None, meta=None, **kwargs):
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
    assert shown[-1][0] == path, "the stable final file must replace partials"
    for partial, _size in shown[:-1]:
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

    from pinky.subs.ai import translator
    monkeypatch.setattr(translator, "available", lambda: True)
    monkeypatch.setattr(translator, "translate",
                        lambda cues, language, on_progress=None, meta=None, **kwargs: cues)

    _path, report = auto.find_and_prepare(MOVIE, ["he", "en", "es"])
    assert report["source_language"] == "es"


def test_the_automatic_path_prefers_an_embedded_track(monkeypatch,
                                                      settings_module):
    """The best case costs nothing: the file already carries the subtitle."""
    import xbmc
    from pinky.subs import auto, embedded

    settings_module.set_many({"subs.auto": "true", "subs.languages": "he,en",
                              "subs.embedded_first": "true"})
    xbmc.JSONRPC_RESULTS["Player.GetProperties"] = {"subtitles": [
        {"index": 0, "language": "eng", "name": "English"},
        {"index": 1, "language": "heb", "name": "Hebrew"},
    ]}

    chosen = []
    monkeypatch.setattr(embedded, "select",
                        lambda index, player=None, name="": chosen.append(index) or True)

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


def test_automatic_search_claims_translation_before_provider_work(
        monkeypatch, settings_module):
    """A later manual request must supersede auto search, not be replaced by it."""
    from pinky.subs.ai import coordinator

    settings_module.set_many({"subs.auto": "true", "subs.languages": "he,en",
                              "subs.embedded_first": "false"})
    observed = []

    def search(meta, languages, player=None, cancelled=None,
               translation_generation=None, **kwargs):
        observed.append(coordinator.current(translation_generation))
        coordinator.begin()  # A manual translation starts while providers run.
        observed.append(cancelled())
        return "", {"reason": "superseded"}

    monkeypatch.setattr(auto, "cached_subtitle", lambda *args: "")
    monkeypatch.setattr(auto, "find_and_prepare", search)
    auto.on_playback_started(object(), MOVIE)

    assert observed == [True, True]


def test_a_forced_embedded_track_is_not_used_automatically(monkeypatch,
                                                           settings_module):
    """Forced tracks caption signs, not dialogue, so they are not a subtitle."""
    import xbmc
    from pinky.subs import auto, embedded

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
    from pinky.subs import service

    seen = []
    monkeypatch.setattr(auto, "download_candidate",
                        lambda cand, expect_language=None:
                        seen.append(expect_language) or [])
    monkeypatch.setattr(service, "_current_meta", lambda: dict(MOVIE))
    service.dispatch(["plugin://plugin.video.pinky/", "1",
                      "?action=download&provider=wizdom&id=x&language=he"])
    assert seen == [None]


def test_an_english_file_labelled_hebrew_is_refused(monkeypatch,
                                                    settings_module):
    """The whole point of the check, at the level it actually runs."""
    from pinky.subs import auto as real

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


def test_an_english_file_labelled_japanese_is_refused(monkeypatch):
    from pinky.subs import auto as real

    english = srt.dump([srt.Cue(1, 1.0, 3.0,
                                "Hello there, how are you today?")]).encode("utf-8")
    monkeypatch.setattr(real, "_modules",
                        lambda: {"wizdom": type("M", (), {
                            "download": staticmethod(lambda c: english)})})
    entry = {"provider": "wizdom", "language": "ja", "release": "x",
             "download": "x"}

    assert real.download_candidate(entry, expect_language="ja") == []


def test_latin_script_languages_are_not_confused_with_english(monkeypatch):
    from pinky.subs import auto as real

    french = srt.dump([srt.Cue(1, 1.0, 3.0,
                               "Bonjour, comment allez-vous aujourd'hui?")]).encode("utf-8")
    monkeypatch.setattr(real, "_modules",
                        lambda: {"wizdom": type("M", (), {
                            "download": staticmethod(lambda c: french)})})
    entry = {"provider": "wizdom", "language": "fr", "release": "x",
             "download": "x"}

    assert real.download_candidate(entry, expect_language="fr") != []


def test_the_same_subtitle_arriving_twice_is_counted_once(monkeypatch,
                                                          settings_module):
    """Asking by hash and asking by title are different questions with
    overlapping answers.

    A duplicate costs more than it looks: `outlook` weighs every candidate
    against every source when the picker opens - 240 sources against 32
    candidates on a real search - so one extra candidate is one extra
    comparison per source, on a projector with a gigabyte of RAM.
    """
    from pinky.subs import auto

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


def test_keyed_opensubtitles_receives_stream_size(monkeypatch, settings_module):
    from pinky.subs import auto
    calls = []

    class Fake(object):
        @staticmethod
        def search(meta, target, languages, video_hash="", file_size=0):
            calls.append((video_hash, file_size))
            return []

    monkeypatch.setattr(auto, "_providers", lambda: [("opensubtitles", Fake)])
    meta = {"type": "movie", "ids": {}, "stream_size": 987654321}
    auto.search_candidates(meta, ["en"], "hash")
    assert calls == [("hash", 987654321)]


def test_candidate_dedupe_is_scoped_by_provider_and_language(
        monkeypatch, settings_module):
    from pinky.subs import auto
    rows = [
        {"provider": "a", "language": "en", "release": "a-en",
         "download": 123},
        {"provider": "b", "language": "en", "release": "b-en",
         "download": 123},
        {"provider": "a", "language": "he", "release": "a-he",
         "download": 123},
    ]

    class Fake(object):
        @staticmethod
        def search(*args, **kwargs):
            return [dict(row) for row in rows]

    monkeypatch.setattr(auto, "_providers", lambda: [("fake", Fake)])
    found = auto.search_candidates({"type": "movie", "ids": {}}, ["he", "en"])
    assert [(row["provider"], row["language"]) for row in found] == [
        ("a", "en"), ("b", "en"), ("a", "he")]


def test_a_candidate_with_no_link_is_still_kept(monkeypatch, settings_module):
    """Deduping on a missing key would collapse them all into one."""
    from pinky.subs import auto

    rows = [{"provider": "x", "language": "he", "release": "a", "download": ""},
            {"provider": "x", "language": "he", "release": "b", "download": ""}]

    class Fake(object):
        @staticmethod
        def search(*args, **kwargs):
            return [dict(r) for r in rows]

    monkeypatch.setattr(auto, "_providers", lambda: [("wizdom", Fake)])
    found = auto.search_candidates({"type": "movie", "ids": {}}, ["he"])
    assert len(found) == 2


def test_wide_search_splits_serial_rest_languages_into_independent_tasks(
        monkeypatch, settings_module):
    from pinky.subs import auto

    calls = []

    class Rest(object):
        @staticmethod
        def search(meta, target, languages, video_hash="", video_size=0):
            calls.append(("rest", list(languages)))
            return []

    class Other(object):
        @staticmethod
        def search(meta, target, languages):
            calls.append(("other", list(languages)))
            return []

    monkeypatch.setattr(auto, "_providers",
                        lambda: [("opensubtitles_rest", Rest), ("wizdom", Other)])
    auto.search_candidates({"type": "movie", "ids": {}},
                           ["en", "es", "ar"], split_languages=True)
    assert [(name, languages) for name, languages in calls if name == "rest"] == [
        ("rest", ["en"]), ("rest", ["es"]), ("rest", ["ar"])]
    assert ("other", ["en", "es", "ar"]) in calls


# --------------------------------------------------------------------------
# the file hash costs the search nothing
# --------------------------------------------------------------------------


def test_the_hash_is_computed_alongside_the_search_not_before_it(monkeypatch,
                                                                settings_module):
    """A second spent hashing is a second of a playing film with no subtitles.

    Three of a dozen providers want the hash. Running it first made every
    other provider wait for it; now it runs while they work, and only the
    three that need it wait at all.
    """
    import time
    from pinky.subs import auto, hasher

    settings_module.set("subs.hash_match", True)

    def slow_hash(url, timeout=None):
        time.sleep(0.4)
        return "abcdef0123456789", 12345

    def slow_provider(*args, **kwargs):
        time.sleep(0.4)
        return []

    monkeypatch.setattr(hasher, "hash_stream", slow_hash)
    monkeypatch.setattr(auto, "_providers",
                        lambda: [("wizdom", _FakeProvider(slow_provider)),
                                 ("subsource", _FakeProvider(slow_provider))])

    meta = {"title": "Dune", "year": 2021, "kind": "movie",
            "stream_url": "http://example.invalid/f.mkv", "ids": {}}
    started = time.time()
    get_hash = auto.video_hash_later(meta)
    auto.search_candidates(meta, ["he", "en"], get_hash)
    elapsed = time.time() - started

    assert get_hash() == "abcdef0123456789"
    assert elapsed < 0.75, "hash and search were serialised (%.2fs)" % elapsed


def test_a_provider_that_wants_the_hash_still_gets_it(monkeypatch,
                                                      settings_module):
    """Overlapping must not mean asking without the thing that was computed."""
    from pinky.subs import auto, hasher

    settings_module.set("subs.hash_match", True)
    monkeypatch.setattr(hasher, "hash_stream",
                        lambda url, timeout=None: ("beef", 999))

    seen = {}

    def remember(meta, target, languages, video_hash="", size=0):
        seen["hash"], seen["size"] = video_hash, size
        return []

    monkeypatch.setattr(auto, "_providers",
                        lambda: [("bsplayer", _FakeProvider(remember))])

    meta = {"title": "Dune", "year": 2021, "kind": "movie",
            "stream_url": "http://example.invalid/f.mkv", "ids": {}}
    auto.search_candidates(meta, ["he"], auto.video_hash_later(meta))
    assert seen == {"hash": "beef", "size": 999}


def test_a_stream_that_cannot_be_hashed_does_not_hold_the_search_up():
    """No stream url, no thread, no wait - the common case for local files."""
    from pinky.subs import auto
    assert auto.video_hash_later({"title": "Dune"})() == ""


class _FakeProvider(object):
    def __init__(self, search):
        self.search = search


# --------------------------------------------------------------------------
# what AI translates from is asked for only when Hebrew does not fit
# --------------------------------------------------------------------------


def test_translation_sources_are_not_searched_while_hebrew_fits(pipeline, monkeypatch):
    from pinky.subs.ai import translator
    monkeypatch.setattr(translator, "available", lambda: True)
    name = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [candidate(name)]
    pipeline["downloads"][name] = srt_bytes()

    auto.find_and_prepare(dict(MOVIE, original_language="en"), ["he", "en"])
    assert pipeline["searched_languages"] == [["he", "en"]]


def test_translation_sources_are_searched_once_no_hebrew_exists(pipeline, monkeypatch):
    from pinky.subs.ai import translator
    monkeypatch.setattr(translator, "available", lambda: True)
    name = "Dune.Part.Two.2024.2160p.BluRay.x265-OTHER"
    pipeline["candidates"] = [candidate(name, language="en")]
    pipeline["downloads"][name] = srt_bytes()

    auto.find_and_prepare(dict(MOVIE, original_language="fr"), ["he", "en"])
    wide = [code for code in auto.AI_SOURCE_LANGUAGES if code != "en"]
    assert pipeline["searched_languages"][:2] == [["he", "en"], wide]
    assert wide.count("fr") == 1, "the show's own language must not repeat"


def test_without_ai_a_downloaded_english_subtitle_is_used(pipeline):
    """It used to be translation material only, so a film with a good English
    file and no Hebrew played with nothing when there was no AI engine."""
    name = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [candidate(name, language="en")]
    pipeline["downloads"][name] = srt_bytes()

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert path.endswith(".en.srt")
    assert report["language"] == "en" and report["translated"] is False


def test_the_files_own_english_comes_before_a_downloaded_one(pipeline):
    """In time by construction, which a download is not."""
    name = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [candidate(name, language="en")]
    pipeline["downloads"][name] = srt_bytes()
    asked = []

    path, report = auto.find_and_prepare(
        MOVIE, ["he", "en"], embedded=lambda code: asked.append(code) or True)
    assert asked == ["en"] and path == "" and report["embedded"] == "en"
    assert pipeline["downloaded"] == [], "nothing downloaded once the file had it"


def test_an_embedded_english_track_is_used_when_nothing_else_exists(pipeline, monkeypatch, settings_module):
    """A fansub release carries English inside. With no Hebrew anywhere the
    add-on used to stop, leaving that track switched off."""
    settings_module.set("subs.embedded_first", "true")
    asked = []
    monkeypatch.setattr(auto, "use_embedded",
                        lambda player, language, cancelled=None: asked.append(language) or language == "en")
    monkeypatch.setattr(auto, "cached_subtitle", lambda meta, language: "")
    auto.on_playback_started(object(), {"title": "Frieren", "type": "episode",
                                        "original_language": "ja"})
    assert asked == ["he", "en"], "Hebrew inside the file first, English only after the search"


def test_a_found_hebrew_subtitle_is_not_replaced_by_embedded_english(monkeypatch, settings_module):
    settings_module.set_many({"subs.auto": "true", "subs.languages": "he,en",
                              "subs.embedded_first": "false"})
    asked = []
    monkeypatch.setattr(auto, "use_embedded",
                        lambda player, language, cancelled=None: asked.append(language) or True)
    monkeypatch.setattr(auto, "cached_subtitle", lambda meta, language: "")
    monkeypatch.setattr(auto, "find_and_prepare",
                        lambda *a, **k: ("/tmp/film.he.srt", {"applied": True}))
    auto.on_playback_started(object(), {"title": "Film", "original_language": "en"})
    assert asked == []


def test_a_subtitle_for_another_episode_is_never_applied(pipeline):
    """A season-zero special has no subtitle anywhere, and the best candidate
    is season one's first episode - which the matcher scores 0, "wrong
    episode". The last-resort fallback used to take it anyway."""
    wrong = "NCIS.S01E01.1080p.BluRay.x265-INFINITY"
    pipeline["candidates"] = [candidate(wrong)]
    pipeline["downloads"][wrong] = srt_bytes()
    special = {"type": "episode", "title": "NCIS", "year": 2003,
               "ids": {"imdb": "tt0364845", "tmdb": 4614},
               "season": 0, "episode": 1,
               "source": {"release": "NCIS.S00E01.1080p.WEB.H264-GRP"}}

    path, report = auto.find_and_prepare(special, ["he", "en"])
    assert path == "", report
    assert pipeline["downloaded"] == [], "and it is not even downloaded"


# --------------------------------------------------------------------------
# a translation inherits its source's timing, so the source has to be in time
# --------------------------------------------------------------------------


def _shifted(cues, seconds):
    return [srt.Cue(c.index, c.start + seconds, c.end + seconds, c.text)
            for c in cues]


def _speech(count=60, step=4.0):
    return [srt.Cue(i + 1, i * step, i * step + 2.0, "line %d" % i)
            for i in range(count)]


class _Budget(object):
    def __init__(self, table):
        self.table = table

    def fetch(self, candidate):
        return self.table.get(id(candidate), [])


def test_the_source_is_re_timed_before_it_is_translated():
    """A translation inherits its source's timing and nothing re-reads it.

    Measured on Hikaru no Go: the only thing found was a Polish subtitle
    matched on release name, and it became a Hebrew subtitle that was out by
    exactly as much as the Polish one was.
    """
    reference = _speech()
    source = _shifted(reference, 12.0)
    fitted = auto.retimed_for_translation(source, reference, "pl")
    assert abs(fitted[0].start - reference[0].start) < 0.5, \
        "the source should have been pulled back into time"


def test_a_source_with_no_reference_is_left_exactly_as_it_was():
    source = _speech()
    assert auto.retimed_for_translation(source, [], "pl") is source


def test_an_unrelated_reference_never_shifts_the_source():
    """A poor fit is left where it was rather than confidently moved.

    The source has already been chosen and the download budget spent, so
    refusing here would leave the viewer with nothing rather than with
    something that may be a second out.
    """
    source = _speech()
    unrelated = [srt.Cue(i + 1, i * 7.3 + 1.1, i * 7.3 + 2.4, "x")
                 for i in range(60)]
    fitted = auto.retimed_for_translation(source, unrelated, "pl")
    assert [c.start for c in fitted] == [c.start for c in source]


def test_a_subtitle_is_never_its_own_timing_reference():
    """The translation path asks about the same languages it translates from."""
    candidate = {"reason": "hash", "language": "en"}
    budget = _Budget({id(candidate): _speech()})
    assert auto.reference_cues({"en": candidate}, ["he", "en"], budget,
                               skip=candidate) == []
    assert auto.reference_cues({"en": candidate}, ["he", "en"], budget) != []


# --------------------------------------------------------------------------
# a log line that was true whatever happened
# --------------------------------------------------------------------------


def test_a_dropped_subtitle_write_is_reported_as_dropped():
    """The facade discards writes once playback has moved on.

    `embedded.select` called through it, got None back whatever happened, and
    logged "selected embedded subtitle track 0" - so a bare screen and a
    switched track left exactly the same line behind. Black Lagoon 1x04 was
    that line with no subtitles under it.
    """
    from pinky import player as player_module
    from pinky.subs import embedded

    owner = player_module.PinkyPlayer()
    owner.meta = {"type": "episode"}
    owner._subtitle_generation = 5
    facade = player_module._PlaybackPlayer(owner, 5, owner.meta)
    assert facade.setSubtitleStream(0) is True
    assert facade.showSubtitles(True) is True

    owner._subtitle_generation = 6           # this playback is no longer current
    assert facade.setSubtitleStream(0) is False
    assert embedded.select(0, facade) is False, \
        "a write nobody applied is not a selected track"


def test_a_live_playback_still_selects():
    from pinky import player as player_module
    from pinky.subs import embedded

    owner = player_module.PinkyPlayer()
    owner.meta = {"type": "episode"}
    owner._subtitle_generation = 1
    facade = player_module._PlaybackPlayer(owner, 1, owner.meta)
    assert embedded.select(0, facade, "English") is True


def test_a_forced_track_is_recognised_in_its_own_language():
    """A German or Spanish rip labels its forced track in its own language.

    Calling that one "the English subtitles" is how a file plays with nothing
    readable on it while the log says a track was selected.
    """
    from pinky.subs import embedded

    for name in ("Forced", "Signs & Songs", "S&S", "Erzwungen",
                 "Subtitulos forzado", "Sottotitoli forzati", "Karaoke"):
        assert embedded.is_partial(name), name
    for name in ("English", "English (Full)", "Hebrew", "Dialogue"):
        assert not embedded.is_partial(name), name
