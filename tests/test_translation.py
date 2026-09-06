"""AI translation must never move a timing, and must survive a sloppy model."""
import json

import pytest

from katan.subs import srt
from katan.subs.ai import translator


class FakeEngine(object):
    """A stand-in model. Configurable to misbehave the way real ones do."""

    def __init__(self, mode="good"):
        self.mode = mode
        self.calls = []

    def complete(self, system_prompt, prompt):
        payload = json.loads(prompt.split("Input:", 1)[1].strip())
        self.calls.append(len(payload))

        if self.mode == "fenced":
            body = {k: "HE:" + v for k, v in payload.items()}
            return "```json\n%s\n```" % json.dumps(body, ensure_ascii=False)
        if self.mode == "prose":
            body = {k: "HE:" + v for k, v in payload.items()}
            return "Sure, here you go:\n%s\nHope that helps." % json.dumps(body)
        if self.mode == "not_json":
            return "I am afraid I cannot do that."
        if self.mode == "empty_half":
            keys = list(payload)
            body = {k: ("HE:" + payload[k] if i < len(keys) // 2 else "")
                    for i, k in enumerate(keys)}
            return json.dumps(body)
        if self.mode == "fails_large":
            # Fails on big chunks, succeeds once the caller splits them.
            if len(payload) > 4:
                raise RuntimeError("context too long")
            return json.dumps({k: "HE:" + v for k, v in payload.items()})
        return json.dumps({k: "HE:" + v for k, v in payload.items()},
                          ensure_ascii=False)


def cues(count=10):
    return [srt.Cue(i + 1, i * 3.0, i * 3.0 + 2.0, "line %d" % i)
            for i in range(count)]


@pytest.fixture
def use_engine(monkeypatch, settings_module):
    settings_module.set_many({"subs.ai.enabled": "true", "subs.ai.chunk": "8"})

    def install(mode="good"):
        engine = FakeEngine(mode)
        monkeypatch.setattr(translator, "engine", lambda: engine)
        return engine

    return install


def test_timings_are_preserved_exactly(use_engine):
    use_engine("good")
    original = cues(12)
    result = translator.translate(original, "he")
    assert len(result) == len(original)
    for before, after in zip(original, result):
        assert after.start == before.start
        assert after.end == before.end
    assert all(c.text.startswith("HE:") for c in result)


def test_code_fences_are_stripped(use_engine):
    use_engine("fenced")
    result = translator.translate(cues(6), "he")
    assert result[0].text == "HE:line 0"


def test_prose_around_the_json_is_ignored(use_engine):
    use_engine("prose")
    result = translator.translate(cues(6), "he")
    assert result[0].text == "HE:line 0"


def test_a_chunk_that_fails_is_split_and_retried(use_engine):
    engine = use_engine("fails_large")
    result = translator.translate(cues(8), "he")
    assert all(c.text.startswith("HE:") for c in result)
    assert max(engine.calls) > min(engine.calls), "the chunk was never split"


def test_untranslated_lines_fall_back_to_the_original(use_engine):
    use_engine("empty_half")
    original = cues(8)
    result = translator.translate(original, "he")
    assert result[0].text.startswith("HE:")
    assert result[-1].text == original[-1].text, "a blank should keep the original"


def test_a_model_that_returns_nothing_useful_raises(use_engine):
    use_engine("not_json")
    with pytest.raises(translator.TranslationError):
        translator.translate(cues(4), "he")


def test_progress_is_reported_per_chunk(use_engine):
    use_engine("good")
    seen = []
    translator.translate(cues(20), "he", on_progress=lambda d, t: seen.append((d, t)))
    assert seen[-1] == (20, 20)
    assert len(seen) == 3, "20 cues at a chunk size of 8 is three chunks"


def test_chunk_keys_stay_absolute_across_chunks(use_engine):
    """Split chunks must still map back onto the right cues."""
    use_engine("good")
    original = cues(20)
    result = translator.translate(original, "he")
    for position, cue in enumerate(result):
        assert cue.text == "HE:line %d" % position


def test_translation_is_off_when_no_engine_is_configured(settings_module):
    settings_module.set("subs.ai.enabled", "false")
    assert translator.engine() is None
    assert translator.available() is False
    with pytest.raises(translator.TranslationError):
        translator.translate(cues(2), "he")
