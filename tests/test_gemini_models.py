# -*- coding: utf-8 -*-
"""The translation engine staying alive while Google retires models under it.

On 22 September 2026 gemini-2.5-flash and gemini-2.5-flash-lite answered every
request from a new key with HTTP 404 - and gemini-2.5-flash was the default
here, so AI translation failed for everybody who made a key that month, while
the key check reported their perfectly good key as invalid. Measured with a
real key afterwards: the -latest aliases answer, and a 300-line chunk dropped
one line, which threw the whole film away.
"""
import json

import pytest

from pinky import http
from pinky.subs import srt
from pinky.subs.ai import context, gemini, translator


class Reply(object):
    def __init__(self, status=200, text='{"0": "x"}'):
        self.status_code = status
        self._text = text

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": self._text}]}}]}


@pytest.fixture
def keyed(settings_module, monkeypatch):
    settings_module.set_many({"subs.ai.gemini_key": "a-key",
                              "subs.ai.gemini_model": ""})
    monkeypatch.setattr(gemini, "_pace", lambda name=None: None)
    return settings_module


def test_every_model_is_an_alias(keyed):
    """Pinning bit twice: 2.5-flash, then 3.6-flash for a real key two days
    later. A version written down here is one that will be retired."""
    assert gemini.model() == "gemini-flash-latest"
    assert gemini.CHAIN[0] == gemini.DEFAULT_MODEL
    assert all(name.endswith("-latest") for name in gemini.CHAIN)
    assert not any("pro" in name for name in gemini.CHAIN), "Pro is not free"


def test_an_overloaded_model_hands_the_request_on(keyed, monkeypatch):
    """gemini-flash-latest answered 503 "high demand" on the day it was the
    fix; splitting the chunk could never help an overloaded server."""
    asked = []

    def post(url, **kwargs):
        name = url.split("/")[-1].split(":")[0]
        asked.append(name)
        return Reply(503) if name != gemini.FAST_MODEL else Reply(text="ok")

    monkeypatch.setattr(http, "post", post)
    assert gemini.complete("system", "prompt") == "ok"
    assert asked == list(gemini.CHAIN)


def test_a_retired_model_falls_back_to_the_alias(keyed, monkeypatch):
    keyed.set("subs.ai.gemini_model", "gemini-2.5-flash")
    asked = []

    def post(url, **kwargs):
        asked.append(url.split("/")[-1].split(":")[0])
        return Reply(404) if "2.5" in url else Reply(text="ok")

    monkeypatch.setattr(http, "post", post)
    assert gemini.complete("system", "prompt") == "ok"
    assert asked == ["gemini-2.5-flash", gemini.DEFAULT_MODEL]


def test_the_key_check_does_not_blame_the_key_for_a_retired_model(keyed, monkeypatch):
    keyed.set("subs.ai.gemini_model", "gemini-2.5-flash")
    asked = []

    def post(url, **kwargs):
        asked.append(url)
        return Reply(404) if "2.5" in url else Reply(text="ok")

    monkeypatch.setattr(http, "post", post)
    assert gemini.test_key("a-key") is True
    assert "gemini-flash-lite-latest" in asked[0]


def _cues(count):
    return [srt.Cue(i + 1, i * 3.0, i * 3.0 + 2.0, "line %d" % i)
            for i in range(count)]


class Recording(object):
    """A backend that translates everything, and says which model was asked."""

    def __init__(self, drop=(), full=False):
        self.calls = []
        self.drop = set(drop)
        self.fast = _Fast(self)
        if full:
            self.full = _Fast(self, "full")

    def _answer(self, prompt, model):
        payload = json.loads(prompt[prompt.index("{"):prompt.rindex("}") + 1])
        self.calls.append((model, len(payload)))
        return json.dumps({key: u"ש" + value for key, value in payload.items()
                           if not (key in self.drop and len(payload) > 1)},
                          ensure_ascii=False)

    def complete(self, system_prompt, prompt, *args, **kwargs):
        return self._answer(prompt, "full")


class _Fast(object):
    def __init__(self, owner, name="fast"):
        self.owner = owner
        self.name = name

    def complete(self, system_prompt, prompt, *args, **kwargs):
        return self.owner._answer(prompt, self.name)


@pytest.fixture
def engine(monkeypatch, settings_module):
    settings_module.set("subs.ai.chunk", "100")
    backend = Recording()
    monkeypatch.setattr(translator, "engine", lambda: backend)
    return backend


def test_every_chunk_goes_to_the_fast_model_and_the_first_is_short(engine):
    """Nothing is on screen until the first chunk returns: 2.5 s for 40 lines
    on the lite model against 43 s for 100 on the full one, measured. And
    nothing after it either, while the full model is busy: Hikaru no Go had
    40 Hebrew lines and then English."""
    translator.translate(_cues(250), "he")
    assert engine.calls == [("fast", 40), ("fast", 100), ("fast", 100), ("fast", 10)]


def test_the_full_model_goes_over_everything_the_fast_one_wrote(monkeypatch,
                                                                 settings_module):
    """The lite model made a male speaker female; the file that is kept
    should not carry that."""
    settings_module.set("subs.ai.chunk", "100")
    backend = Recording(full=True)
    monkeypatch.setattr(translator, "engine", lambda: backend)
    translator.translate(_cues(250), "he")
    assert backend.calls[4:] == [("full", 40), ("full", 100), ("full", 100), ("full", 10)]


def test_a_dropped_line_is_asked_for_again_instead_of_losing_the_film(
        monkeypatch, settings_module):
    settings_module.set("subs.ai.chunk", "100")
    backend = Recording(drop={"57"})
    monkeypatch.setattr(translator, "engine", lambda: backend)
    out = translator.translate(_cues(140), "he")
    assert all(cue.text.startswith(u"ש") for cue in out)
    assert ("fast", 1) in backend.calls, "only the missing line is re-sent"


def test_an_exact_file_subtitle_is_translated_before_a_guessed_arabic_one():
    """A translation keeps its source's timings. The +25 Arabic bonus was
    beating a hash-matched English file, 76 + 25 against 100 + 0."""
    winners = {"he": {"score": 55},
               "en": {"score": 100, "reason": "hash", "release": "exact"},
               "ar": {"score": 76, "reason": "name", "release": "guessed"}}
    ranked = context.rank_translation_sources(winners, ["he", "en", "ar"])
    assert ranked[0][1]["release"] == "exact"
    candidates = [dict(winners["ar"], language="ar", provider="b", download=2),
                  dict(winners["en"], language="en", provider="a", download=1)]
    ranked = context.rank_translation_candidates(candidates, "he")
    assert ranked[0][1]["release"] == "exact"


def test_between_two_guesses_arabic_still_wins_a_close_call():
    winners = {"en": {"score": 90, "reason": "name"},
               "ar": {"score": 76, "reason": "name"}}
    ranked = context.rank_translation_sources(winners, ["he", "en", "ar"])
    assert ranked[0][0] == "ar"
