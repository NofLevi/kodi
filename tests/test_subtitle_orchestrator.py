# -*- coding: utf-8 -*-
"""What happens when the things the subtitle search depends on misbehave.

Every other subtitle test replaces `auto.search_candidates` and
`auto.download_candidate` wholesale with fakes that only ever return data. That
is the right way to test the *decision*, and it means the code between a
provider and the decision - the worker pool, the guard, the dedupe, the
ranking - has never been asked what it does when a provider misbehaves.

So these tests inject at the real boundary: the provider modules themselves.
Everything above them is the shipping code.

The rule they all check is one rule. **A subtitle is a convenience, and no
failure of one may cost more than that subtitle.** Something unexpected is
allowed to produce no subtitles; it is not allowed to produce a crash, a hang,
a wrong subtitle, or a provider written off for the rest of the operation.
"""
import time

import pytest

from pinky.subs import auto, srt


MOVIE = {
    "type": "movie",
    "ids": {"imdb": "tt0110912", "tmdb": 680},
    "title": "Pulp Fiction",
    "year": 1994,
    "stream_url": "",
    "source": {"release": "Pulp.Fiction.1994.1080p.BluRay.x264-AAA",
               "group": "aaa", "quality": "1080p"},
}


def candidate(release, provider="wizdom", language="he", **kwargs):
    entry = {"provider": provider, "language": language, "release": release,
             "download": "%s/%s" % (provider, release)}
    entry.update(kwargs)
    return entry


class FakeProvider(object):
    """A provider module that can be told to misbehave."""

    def __init__(self, name, result=None, raises=None):
        self.NAME = name
        self.result = result
        self.raises = raises
        self.searched = 0
        self.downloaded = 0

    def search(self, meta, target, languages, *args):
        self.searched += 1
        if self.raises is not None:
            raise self.raises
        return self.result

    def download(self, candidate, expect_language=None):
        self.downloaded += 1
        if self.raises is not None:
            raise self.raises
        return b""


@pytest.fixture
def providers(monkeypatch, settings_module):
    """Replace the provider modules, keeping everything above them real."""
    settings_module.set_many({
        "subs.auto": "true",
        "subs.languages": "he,en",
        "subs.threshold": "70",
        "subs.hash_match": "false",
        "subs.ai.enabled": "false",
        "subs.provider.wizdom": "true",
        "subs.provider.bsplayer": "true",
        "subs.provider.opensubtitles_rest": "true",
        "subs.provider.opensubtitles": "false",
        "subs.provider.subsource": "false",
        "subs.provider.ktuvit": "false",
    })
    registry = {}

    def install(**modules):
        registry.clear()
        registry.update(modules)
        monkeypatch.setattr(auto, "_MODULES", registry)
        return registry

    return install


# --------------------------------------------------------------------------
# a provider that misbehaves must cost only itself
# --------------------------------------------------------------------------


def test_one_provider_raising_does_not_take_the_search_with_it(providers):
    good = FakeProvider("wizdom", [candidate("Pulp.Fiction.1994.1080p")])
    providers(wizdom=good,
              bsplayer=FakeProvider("bsplayer", raises=RuntimeError("boom")),
              opensubtitles_rest=FakeProvider("opensubtitles_rest",
                                              raises=ValueError("nonsense")))

    found = auto.search_candidates(MOVIE, ["he", "en"])
    assert [c["release"] for c in found] == ["Pulp.Fiction.1994.1080p"]


def test_every_provider_raising_is_no_subtitles_not_an_exception(providers):
    providers(wizdom=FakeProvider("wizdom", raises=RuntimeError("boom")),
              bsplayer=FakeProvider("bsplayer", raises=IOError("gone")),
              opensubtitles_rest=FakeProvider("opensubtitles_rest",
                                              raises=KeyError("shape")))
    assert auto.search_candidates(MOVIE, ["he", "en"]) == []


@pytest.mark.parametrize("answer", [
    None,                       # a provider that forgot to return
    "not a list",               # a string is iterable, which is the trap
    [None, 3, "x"],             # entries that are not candidates
    [{}],                       # a dict with nothing in it
    [{"provider": "wizdom"}],   # no language, no release, no download
])
def test_a_provider_answering_nonsense_is_survived(providers, answer):
    """Anything that is not a list of candidate dicts must be ignored."""
    providers(wizdom=FakeProvider("wizdom", answer),
              bsplayer=FakeProvider("bsplayer", []),
              opensubtitles_rest=FakeProvider("opensubtitles_rest", []))
    found = auto.search_candidates(MOVIE, ["he", "en"])
    assert isinstance(found, list)
    for entry in found:
        assert isinstance(entry, dict)


def test_a_candidate_carrying_junk_is_ranked_without_raising(providers):
    """The dedupe checks the *type*, nothing checks the values.

    A provider can hand back a well-formed dict whose fields are the wrong
    shape - a download count as "1,234", a score as a word - and the matcher
    calls int() on it. That is one provider's bad day turning into no
    subtitles at all for the playback.
    """
    from pinky.subs import matcher

    junk = [
        candidate("Pulp.Fiction.1994.1080p.BluRay.x264-AAA", downloads="1,234"),
        candidate("Pulp.Fiction.1994.720p", downloads=None),
        candidate("Pulp.Fiction.1994.DVDRip", downloads="many"),
        candidate("Pulp.Fiction.1994.WEB", sync_percent="high"),
    ]
    providers(wizdom=FakeProvider("wizdom", junk),
              bsplayer=FakeProvider("bsplayer", []),
              opensubtitles_rest=FakeProvider("opensubtitles_rest", []))

    found = auto.search_candidates(MOVIE, ["he", "en"])
    assert len(found) == 4
    ranked = matcher.rank(found, matcher.target_from(MOVIE), "", ["he", "en"])
    assert len(ranked) == 4, "a bad field must not lose the candidate"


def test_a_provider_that_hangs_does_not_hold_the_playback(providers):
    """The deadline is the rule the whole add-on rests on."""

    class Slow(FakeProvider):
        def search(self, *args):
            time.sleep(30)
            return []

    fast = FakeProvider("wizdom", [candidate("Pulp.Fiction.1994.1080p")])
    providers(wizdom=fast, bsplayer=Slow("bsplayer"),
              opensubtitles_rest=Slow("opensubtitles_rest"))

    started = time.time()
    found = auto.search_candidates(MOVIE, ["he", "en"])
    elapsed = time.time() - started

    assert [c["release"] for c in found] == ["Pulp.Fiction.1994.1080p"]
    assert elapsed < 15.0, "took %.1fs; the 10s deadline did not hold" % elapsed


# --------------------------------------------------------------------------
# the correlator is fed by strangers
# --------------------------------------------------------------------------


def test_an_alignment_that_raises_leaves_the_subtitle_at_its_own_timing(
        monkeypatch):
    """Refusing to align is normal; raising must not end the search.

    `consensus.agree` has always wrapped its own `sync.fit` call for this
    reason while the two main callers did not, so a failure in the most
    data-driven code here reached the top of the playback's subtitle work.
    """
    from pinky.subs import sync

    cues = [srt.Cue(i + 1, i * 4.0, i * 4.0 + 2.0, "line %d" % i)
            for i in range(40)]

    def explode(*args, **kwargs):
        raise MemoryError("a 36 Mbit integer used to get here")

    monkeypatch.setattr(sync, "synchronise", explode)
    fitted, result = auto._synchronise_safely(cues, cues)

    assert fitted is cues, "the original timing should survive"
    assert result["applied"] is False
    assert result["confidence"] == 0.0


# --------------------------------------------------------------------------
# what Kodi itself answers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["error", "no_result", "garbage"])
def test_kodi_refusing_to_answer_is_no_tracks_not_a_crash(answer):
    """Real Kodi returns an error envelope when nothing is playing.

    The stub could only ever succeed, so the guards here were written from a
    guess at Kodi rather than from Kodi. Now they are asserted.
    """
    import xbmc
    from pinky.subs import embedded

    xbmc.JSONRPC_RESULTS["Player.GetProperties"] = {
        "error": xbmc.JSONRPC_ERROR,
        "no_result": xbmc.JSONRPC_NO_RESULT,
        "garbage": xbmc.JSONRPC_GARBAGE,
    }[answer]

    assert embedded.streams() == []
    assert embedded.audio_streams() == []
    assert embedded.candidates(["he", "en"]) == []
    assert embedded.audio_languages() == []


@pytest.mark.parametrize("entry", [
    {"index": "not a number", "language": "heb", "name": "Hebrew"},
    {"index": None, "language": "heb", "name": "Hebrew"},
    {"index": {"nested": 1}, "language": "heb", "name": "Hebrew"},
])
def test_a_track_with_an_unusable_index_is_skipped_not_fatal(entry):
    """`int(entry.get("index"))` sat outside the try that guards the parse.

    This runs inside `coordinator.commit`, which holds a process-wide lock, so
    a container whose track index is not a number took the lock down with it
    on the way out of the automatic path.
    """
    import xbmc
    from pinky.subs import embedded

    good = {"index": 1, "language": "eng", "name": "English"}
    xbmc.JSONRPC_RESULTS["Player.GetProperties"] = {"subtitles": [entry, good],
                                                    "audiostreams": [entry]}

    streams = embedded.streams()
    assert [s["language"] for s in streams] == ["en"], \
        "the unusable track should be dropped and the good one kept"
    assert embedded.audio_streams() == []


def test_a_subtitle_list_that_is_not_a_list_is_survived():
    import xbmc
    from pinky.subs import embedded

    xbmc.JSONRPC_RESULTS["Player.GetProperties"] = {"subtitles": "nonsense",
                                                    "audiostreams": 7}
    assert embedded.streams() == []
    assert embedded.audio_streams() == []


# --------------------------------------------------------------------------
# nothing may leave the viewer looking at something that will not go away
# --------------------------------------------------------------------------


@pytest.mark.parametrize("handle", ["not a number", "", "12x"])
def test_a_bad_handle_still_closes_the_subtitle_dialog(handle):
    """Kodi's "searching for subtitles" spinner ends only when we close it.

    `dispatch` puts that close in a finally and its docstring says why - but
    it read the handle *above* the try, so a handle that is not a number
    skipped the finally entirely and left the spinner up with no way out but
    closing the dialog by hand.
    """
    import xbmcplugin
    from pinky.subs import service

    xbmcplugin.reset()
    service.dispatch(["plugin://plugin.video.pinky", handle, "?action=search"])
    assert xbmcplugin.ENDED, "the directory was never closed"


def test_a_failure_setting_up_partials_still_closes_the_progress_bar(
        monkeypatch, settings_module):
    """The background progress bar has exactly one close, in a finally.

    `_partial_slots` ran before that try. It does `int(meta["season"])` on a
    value that has round-tripped through JSON from a window property, and
    makes a directory in the profile - either raising left a progress bar on
    screen with nothing alive to close it.
    """
    import xbmcgui
    from pinky.subs.ai import translator

    closed = []

    class Bar(object):
        def create(self, *args, **kwargs):
            pass

        def update(self, *args, **kwargs):
            pass

        def close(self):
            closed.append(True)

    monkeypatch.setattr(xbmcgui, "DialogProgressBG", Bar)
    monkeypatch.setattr(auto, "_partial_slots",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ValueError("season was 'special'")))
    monkeypatch.setattr(translator, "translate",
                        lambda *a, **k: pytest.fail("should not get this far"))

    cues = [srt.Cue(1, 0.0, 2.0, "line")]
    with pytest.raises(ValueError):
        auto._translate_progressively(cues, "he", None, {"type": "movie"},
                                      cancelled=None, generation=0)

    assert closed, "the progress bar was left on the screen"


# --------------------------------------------------------------------------
# a failure must be blamed on the thing that actually failed
# --------------------------------------------------------------------------


def test_a_bug_in_a_provider_does_not_disable_that_provider(providers):
    """An exception here is our fault, or one payload's - not the service's.

    `_DownloadBudget` writes a provider off after two *empty* answers, which
    is right when the service is refusing. But the exception path recorded
    itself as "served nothing", so a one-line code bug - a key that moved, a
    shape that changed - disabled the provider's entire catalogue for the
    playback and was reported as "no subtitles".
    """
    broken = FakeProvider("wizdom", raises=AttributeError("'NoneType' has no .get"))
    providers(wizdom=broken, bsplayer=FakeProvider("bsplayer", []),
              opensubtitles_rest=FakeProvider("opensubtitles_rest", []))

    budget = auto._DownloadBudget(3)
    for index in range(4):
        budget.fetch(candidate("Release.%d" % index), expect_language="he")

    assert broken.downloaded == 4, "every attempt should still have been made"
    assert not budget.exhausted(candidate("Release.9")), \
        "the provider was written off for a bug in our own reading of it"


def test_a_provider_returning_text_instead_of_bytes_is_survived(providers):
    """`decode` assumes bytes and `parse` assumes text that behaves."""

    class Stringly(FakeProvider):
        def download(self, candidate, expect_language=None):
            self.downloaded += 1
            return u"1\n00:00:01,000 --> 00:00:02,000\nline\n"

    providers(wizdom=Stringly("wizdom"), bsplayer=FakeProvider("bsplayer", []),
              opensubtitles_rest=FakeProvider("opensubtitles_rest", []))
    assert auto.download_candidate(candidate("Release"),
                                   expect_language="he") == []


def test_a_look_up_that_failed_is_not_remembered_as_no_subtitles(
        monkeypatch, settings_module):
    """A failure is not a result, and this one was cached for an hour.

    One bad moment - a provider down, the wifi dropping - told the title it
    had no Hebrew subtitles until the TTL expired, and reopening the picker
    could not undo it.
    """
    from pinky.subs import outlook

    calls = {"n": 0}

    def flaky(meta, languages, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IOError("the wifi dropped")
        return [candidate("Pulp.Fiction.1994.1080p")]

    monkeypatch.setattr(auto, "search_candidates", flaky)

    assert outlook.candidates(MOVIE) == []
    again = outlook.candidates(MOVIE)
    assert [c["release"] for c in again] == ["Pulp.Fiction.1994.1080p"], \
        "the failure was cached and the retry never happened"


# --------------------------------------------------------------------------
# work for a playback that is over must leave nothing behind
# --------------------------------------------------------------------------


def test_a_superseded_search_writes_no_file(monkeypatch, settings_module):
    """Cancelling was checked after `find_and_prepare` returned, not inside.

    So a superseded search still wrote its subtitle *and* still ran
    `prune_cache()`, which deletes other titles' files to stay under the cap.
    Pressing stop and starting something else could evict the very subtitle
    the new playback was about to reuse.
    """
    settings_module.set_many({"subs.languages": "he,en", "subs.threshold": "70",
                             "subs.hash_match": "false", "subs.ai.enabled": "false"})
    import os
    from pinky.subs import auto as auto_module

    good = candidate("Pulp.Fiction.1994.1080p.BluRay.x264-AAA")
    cues = [srt.Cue(i + 1, i * 4.0, i * 4.0 + 2.0, "line") for i in range(30)]
    monkeypatch.setattr(auto_module, "search_candidates",
                        lambda *a, **k: [dict(good)])
    monkeypatch.setattr(auto_module, "download_candidate",
                        lambda *a, **k: list(cues))
    monkeypatch.setattr(auto_module, "video_hash_later",
                        lambda meta: (lambda: ""))

    # Cancelled from the moment the candidates are back.
    path, report = auto_module.find_and_prepare(
        MOVIE, ["he", "en"], cancelled=lambda: True)

    assert path == ""
    folder = auto_module.subtitle_dir()
    left = os.listdir(folder) if os.path.isdir(folder) else []
    assert left == [], "a cancelled playback left %r behind" % left


# --------------------------------------------------------------------------
# somebody else's service is allowed to be strange, not to cost us the search
# --------------------------------------------------------------------------


def test_a_charset_python_does_not_have_is_not_an_exception():
    """The charset is a stranger's string used as a codec name.

    A typo, or one this Python was not built with, made `.text` raise
    `LookupError` - which is not what the callers guard for, so it escaped
    `get_json` and reached the provider as an exception rather than as "no
    subtitles from this one".
    """
    from pinky import urlsession

    response = urlsession.Response(
        "https://example.com", 200,
        {"Content-Type": "application/json; charset=utf-lolno"},
        b'{"ok": true}')

    assert response.encoding == "utf-8", "an unknown charset must fall back"
    assert response.text == '{"ok": true}'
    assert response.json() == {"ok": True}


def test_a_real_charset_is_still_honoured():
    """The fallback must not quietly break the encodings that do exist -
    Hebrew subtitle sites serve windows-1255 and mean it."""
    from pinky import urlsession

    hebrew = u"שלום"
    response = urlsession.Response(
        "https://example.com", 200,
        {"Content-Type": "text/html; charset=windows-1255"},
        hebrew.encode("windows-1255"))

    assert response.encoding == "windows-1255"
    assert response.text == hebrew


def test_a_provider_may_not_hold_a_worker_thread_past_the_deadline(monkeypatch):
    """One of four shared threads, against a ten second search deadline.

    Three hosts, each retried once at a fifteen second read timeout, is up to
    120 seconds for one SOAP call and 240 for a search. The deadline abandons
    the result but cannot stop a running task, so everything else - including
    the source search - queues behind a service that is simply down.
    """
    from pinky import http
    from pinky.subs.providers import bsplayer

    def slow_post(url, **kwargs):
        time.sleep(5)
        return None

    monkeypatch.setattr(http, "post", slow_post)
    started = time.time()
    assert bsplayer._call("logIn", "") == ""
    elapsed = time.time() - started
    assert elapsed < bsplayer.MAX_CALL_SECONDS + 6, \
        "held the thread for %.0fs" % elapsed


def test_a_provider_may_not_return_an_unbounded_candidate_list():
    """`outlook` weighs every candidate against every source when the picker
    opens, so one uncapped provider is measured in hundreds of thousands of
    comparisons. Every other provider caps; this one did not."""
    from pinky.subs.providers import ktuvit

    assert ktuvit.MAX_RESULTS <= 60


# --------------------------------------------------------------------------
# the same question is not worth asking twice
# --------------------------------------------------------------------------


def test_one_playback_asks_the_providers_once_per_question(providers):
    """Tracing one film found the providers asked four to seven times.

    The picker's outlook, then what AI could translate from, then the English
    rows, then the search when the film actually starts, then timing evidence
    - and `outlook` was the only one caching anything, under a key none of the
    others match. On a projector each round is real seconds of somebody's
    evening.
    """
    wizdom = FakeProvider("wizdom", [candidate("Pulp.Fiction.1994.1080p")])
    providers(wizdom=wizdom, bsplayer=FakeProvider("bsplayer", []),
              opensubtitles_rest=FakeProvider("opensubtitles_rest", []))

    first = auto.search_candidates(MOVIE, ["he", "en"])
    second = auto.search_candidates(MOVIE, ["he", "en"])

    assert wizdom.searched == 1, "asked %d times" % wizdom.searched
    assert [c["release"] for c in second] == [c["release"] for c in first]
    assert second[0] is not first[0], \
        "a remembered candidate must be a copy - the matcher writes its score on"


def test_a_different_question_is_still_asked(providers):
    wizdom = FakeProvider("wizdom", [candidate("Pulp.Fiction.1994.1080p")])
    providers(wizdom=wizdom, bsplayer=FakeProvider("bsplayer", []),
              opensubtitles_rest=FakeProvider("opensubtitles_rest", []))

    auto.search_candidates(MOVIE, ["he", "en"])
    auto.search_candidates(MOVIE, ["ar", "es"])          # the AI round
    auto.search_candidates(dict(MOVIE, season=2, episode=3), ["he", "en"])

    assert wizdom.searched == 3


def test_a_search_that_found_nothing_is_asked_again(providers):
    """A failure is not a result. Remembering "none" would turn one provider
    outage into a whole playback with no subtitles, and the next round is
    exactly where it would have recovered."""
    empty = FakeProvider("wizdom", [])
    providers(wizdom=empty, bsplayer=FakeProvider("bsplayer", []),
              opensubtitles_rest=FakeProvider("opensubtitles_rest", []))

    assert auto.search_candidates(MOVIE, ["he", "en"]) == []
    assert auto.search_candidates(MOVIE, ["he", "en"]) == []
    assert empty.searched == 2


def test_an_oversized_subtitle_is_refused_before_it_is_built():
    """The per-cue ceiling fires only after twenty thousand Cue objects exist.

    That is about 5 MB of objects on a device with a few hundred, spent on a
    file already decided against. Counting the arrows first is one C-level
    scan of a string we are holding anyway.
    """
    import tracemalloc

    absurd = "".join(
        "%d\n00:00:%06.3f --> 00:00:%06.3f\nline %d\n"
        % (i + 1, (i * 2.0) % 60, (i * 2.0 + 1.5) % 60, i)
        for i in range(25000))

    tracemalloc.start()
    try:
        parsed = srt.parse(absurd)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert parsed == []
    assert peak < 512 * 1024, \
        "built %.1f MB before refusing it" % (peak / 1024.0 / 1024.0)


def test_a_normal_film_is_untouched_by_that_guard():
    normal = "".join(
        "%d\n00:00:%06.3f --> 00:00:%06.3f\nline %d\n"
        % (i + 1, (i * 2.0) % 60, (i * 2.0 + 1.5) % 60, i)
        for i in range(1800))
    assert len(srt.parse(normal)) == 1800


def test_a_candidates_stem_is_computed_once_not_once_per_source():
    """The picker weighs every subtitle against every release.

    So one candidate's stem was recomputed once per source - profiled at
    76,800 calls for about 272 distinct strings on a 240x32 search, and the
    largest single cost in the pass. `release.normalise` already says this in
    its own docstring; `_stem` had not acted on it.
    """
    from pinky.subs import matcher

    matcher._stem.cache_clear()
    name = "Pulp.Fiction.1994.1080p.BluRay.x264-AMIABLE.heb.srt"
    for _ in range(500):
        matcher._stem(name)

    info = matcher._stem.cache_info()
    assert info.misses == 1, "recomputed %d times" % info.misses
    assert info.hits == 499


# --------------------------------------------------------------------------
# a provider that is gone
# --------------------------------------------------------------------------


def test_bsplayer_ships_off():
    """Every host stopped answering in September 2026.

    All three resolve to one address that accepts no connection, so each
    search paid three four-second timeouts for nothing - and its call budget
    was twelve seconds against a ten second search deadline, which is what
    `dropped: ktuvit` in the log was really about.
    """
    from pinky import settings

    assert settings.DEFAULTS["subs.provider.bsplayer"] == "false"


def test_no_provider_may_outlast_the_search_deadline():
    """A worker that runs longer than the deadline drops what queued behind it.

    `search_candidates` gives the whole search ten seconds. bsplayer allowed
    itself twelve, so one call could outlive the search that asked for it.
    """
    import inspect

    from pinky.subs import auto
    from pinky.subs.providers import bsplayer

    source = inspect.getsource(auto.search_candidates)
    assert "deadline=10.0" in source, "the deadline moved; re-check this"
    assert bsplayer.MAX_CALL_SECONDS < 10.0, (
        "bsplayer may spend %.1fs against a 10.0s deadline"
        % bsplayer.MAX_CALL_SECONDS)


def test_switching_it_on_says_why_nothing_comes_back(monkeypatch):
    """Once per process, not once per search - the log has to stay readable.

    It still asks, the way SubSource still asks: the service being gone today
    is not a reason to make its return unreachable.
    """
    said = []
    from pinky import kodi
    from pinky.subs.providers import bsplayer

    monkeypatch.setattr(kodi, "log", lambda message, *a, **k: said.append(message))
    monkeypatch.setattr(bsplayer.http, "post", lambda *a, **k: None)
    del bsplayer._SAID[:]
    for _ in range(3):
        assert bsplayer.search({}, None, ["en"], "abc", 123) == []
    warnings = [line for line in said if "stopped answering" in line]
    assert len(warnings) == 1, said


def test_subsource_stops_asking_once_it_has_been_refused(monkeypatch, no_network):
    """It ships off, but a profile that has it on from an older default was
    paying a request per search on one of four workers under a ten second
    deadline - to be told the same thing every time."""
    from pinky.subs.providers import subsource

    subsource._refused.clear()
    asked = []

    class Refused(object):
        status_code = 404

        def json(self):
            return {}

    monkeypatch.setattr(subsource.http, "post",
                        lambda *a, **k: asked.append(a) or Refused())
    meta = {"title": "A Film", "year": 2020, "type": "movie"}

    for _ in range(4):
        subsource.search(meta, "he", ["he"])

    assert len(asked) == 1, "asked %d times after being refused once" % len(asked)
    subsource._refused.clear()
