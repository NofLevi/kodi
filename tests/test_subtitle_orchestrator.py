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

from katan.subs import auto, srt


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
    from katan.subs import matcher

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
    from katan.subs import sync

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
    from katan.subs import embedded

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
    from katan.subs import embedded

    good = {"index": 1, "language": "eng", "name": "English"}
    xbmc.JSONRPC_RESULTS["Player.GetProperties"] = {"subtitles": [entry, good],
                                                    "audiostreams": [entry]}

    streams = embedded.streams()
    assert [s["language"] for s in streams] == ["en"], \
        "the unusable track should be dropped and the good one kept"
    assert embedded.audio_streams() == []


def test_a_subtitle_list_that_is_not_a_list_is_survived():
    import xbmc
    from katan.subs import embedded

    xbmc.JSONRPC_RESULTS["Player.GetProperties"] = {"subtitles": "nonsense",
                                                    "audiostreams": 7}
    assert embedded.streams() == []
    assert embedded.audio_streams() == []
