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
        self.prompts = []

    def complete(self, system_prompt, prompt):
        payload = json.loads(prompt.split("Input:", 1)[1].strip())
        self.calls.append(len(payload))
        self.prompts.append(prompt)

        if self.mode == "fenced":
            body = {k: "HE:" + v for k, v in payload.items()}
            return "```json\n%s\n```" % json.dumps(body, ensure_ascii=False)
        if self.mode == "prose":
            body = {k: "HE:" + v for k, v in payload.items()}
            return "Sure, here you go:\n%s\nHope that helps." % json.dumps(body)
        if self.mode == "not_json":
            return "I am afraid I cannot do that."
        if self.mode == "echo":
            return json.dumps(payload, ensure_ascii=False)
        if self.mode == "empty_half":
            keys = list(payload)
            body = {k: ("HE:" + payload[k] if i < len(keys) // 2 else "")
                    for i, k in enumerate(keys)}
            return json.dumps(body)
        if self.mode == "missing_one":
            keys = list(payload)
            return json.dumps({k: "HE:" + payload[k] for k in keys[:-1]})
        if self.mode == "missing_one_extra":
            keys = list(payload)
            body = {k: "HE:" + payload[k] for k in keys[:-1]}
            body["unexpected"] = "HE:invented"
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


def test_hebrew_gender_guidance_is_not_claimed_for_english(use_engine):
    engine = use_engine("good")
    translator.translate(cues(2), "en")
    assert "English marks the speaker's gender" not in engine.prompts[0]


def test_wide_target_codes_use_real_language_names(use_engine):
    engine = use_engine("good")
    translator.translate(cues(2), "tr")
    assert "Translate the subtitle lines below into Turkish" in engine.prompts[0]


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


def test_echoed_source_is_not_reported_as_translation(use_engine):
    use_engine("echo")
    with pytest.raises(translator.TranslationError):
        translator.translate(cues(8), "he")


def test_materially_partial_translation_is_not_reported_as_complete(use_engine):
    use_engine("empty_half")
    with pytest.raises(translator.TranslationError):
        translator.translate(cues(8), "he")


def test_even_one_untranslated_line_rejects_the_final_file(
        use_engine, settings_module):
    settings_module.set("subs.ai.chunk", "10")
    use_engine("missing_one")
    with pytest.raises(translator.TranslationError):
        translator.translate(cues(10), "he")


def test_unexpected_key_cannot_hide_one_untranslated_line(
        use_engine, settings_module):
    settings_module.set("subs.ai.chunk", "10")
    use_engine("missing_one_extra")
    with pytest.raises(translator.TranslationError):
        translator.translate(cues(10), "he")


def test_a_model_that_returns_nothing_useful_raises(use_engine):
    engine = use_engine("not_json")
    with pytest.raises(translator.TranslationError):
        translator.translate(cues(80), "he")
    assert len(engine.calls) <= 16, "recursive splitting amplified one failure"


def test_long_valid_translation_is_not_blocked_by_retry_cap(
        use_engine, settings_module):
    settings_module.set("subs.ai.chunk", "20")
    engine = use_engine("good")
    result = translator.translate(cues(801), "he")
    assert len(result) == 801
    assert len(engine.calls) == 41


def test_new_translation_supersedes_previous_process_wide_job():
    from katan.subs.ai import coordinator

    first = coordinator.begin()
    second = coordinator.begin()
    assert not coordinator.current(first)
    assert coordinator.current(second)


def test_cancelled_translation_makes_no_model_request(use_engine):
    engine = use_engine("good")
    with pytest.raises(translator.TranslationError):
        translator.translate(cues(8), "he", cancelled=lambda: True)
    assert engine.calls == []


def test_cancellation_during_final_model_call_discards_its_reply(
        monkeypatch, settings_module):
    state = {"cancelled": False}

    class Engine(object):
        def complete(self, system_prompt, prompt):
            payload = json.loads(prompt.split("Input:", 1)[1].strip())
            state["cancelled"] = True
            return json.dumps({key: "HE:" + value
                               for key, value in payload.items()})

    settings_module.set_many({"subs.ai.enabled": "true", "subs.ai.chunk": "20"})
    monkeypatch.setattr(translator, "engine", lambda: Engine())
    with pytest.raises(translator.TranslationCancelled):
        translator.translate(cues(2), "he",
                             cancelled=lambda: state["cancelled"])


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


def test_partial_results_are_offered_after_each_chunk(use_engine):
    """A viewer should start watching before the whole film is translated."""
    use_engine("good")
    original = cues(20)
    snapshots = []

    def on_progress(done, total, partial=None):
        snapshots.append((done, list(partial) if partial else None))

    translator.translate(original, "he", on_progress=on_progress)

    assert len(snapshots) == 3, "20 cues at a chunk size of 8 is three chunks"
    first_done, first_partial = snapshots[0]
    assert first_partial is not None
    assert len(first_partial) == len(original), \
        "a partial must be a complete, playable file"
    assert first_partial[0].text.startswith("HE:"), "the first chunk is done"
    assert first_partial[-1].text == original[-1].text, \
        "untranslated lines keep their original text"


def test_partial_results_keep_the_original_timings(use_engine):
    use_engine("good")
    original = cues(20)
    seen = []
    translator.translate(original, "he",
                         on_progress=lambda d, t, p=None: seen.append(p))
    for partial in seen:
        for before, after in zip(original, partial):
            assert after.start == before.start and after.end == before.end


def test_a_progress_callback_that_wants_only_counts_still_works(use_engine):
    """Older callers pass a two argument function; that must not break."""
    use_engine("good")
    counts = []
    translator.translate(cues(10), "he",
                         on_progress=lambda done, total: counts.append(done))
    assert counts and counts[-1] == 10


def test_a_failing_progress_callback_does_not_stop_the_translation(use_engine):
    use_engine("good")

    def broken(done, total, partial=None):
        raise RuntimeError("the UI blew up")

    result = translator.translate(cues(10), "he", on_progress=broken)
    assert all(c.text.startswith("HE:") for c in result)
