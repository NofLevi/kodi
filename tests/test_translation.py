"""AI translation must never move a timing, and must survive a sloppy model."""
import json

import pytest

from pinky.subs import srt
from pinky.subs.ai import translator


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
        # Only this engine: Google's free one would otherwise answer for a
        # fake that fails, over the network.
        monkeypatch.setattr(translator, "engines", lambda: [engine])
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
    from pinky.subs.ai import coordinator

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


def test_a_model_that_answers_404_is_not_asked_again(monkeypatch):
    """404 is the API saying the name is not served here, and it is permanent.

    Measured on a real translation: the configured model answered 404 and was
    asked again for every chunk, three requests deep each time, for over a
    minute of a film that was already playing.
    """
    from pinky.subs.ai import gemini

    gemini._RETIRED.clear()
    asked = []

    def answer(system_prompt, prompt, timeout, name):
        asked.append(name)
        if name == gemini.DEFAULT_MODEL:
            raise gemini.ModelRetired("HTTP 404")
        return "ok"

    monkeypatch.setattr(gemini, "_complete", answer)
    try:
        assert gemini.complete("s", "p") == "ok"
        first = list(asked)
        assert gemini.DEFAULT_MODEL in first
        del asked[:]
        assert gemini.complete("s", "p") == "ok"
        assert gemini.DEFAULT_MODEL not in asked
    finally:
        gemini._RETIRED.clear()


def test_an_overloaded_model_steps_back_but_is_not_struck_off(monkeypatch):
    """503 says busy, not gone - so it is not retired like a 404, but nor is
    it asked first again on the next chunk. Measured on 6 October 2026:
    flash answered 503 for hours while flash-lite translated 80 lines in
    four seconds, and asking flash first made every chunk wait it out."""
    from pinky.subs.ai import gemini

    gemini._RETIRED.clear()
    gemini._BUSY.clear()
    asked = []

    def answer(system_prompt, prompt, timeout, name):
        asked.append(name)
        if name == gemini.DEFAULT_MODEL:
            raise gemini.ModelUnavailable("HTTP 503")
        return "ok"

    monkeypatch.setattr(gemini, "_complete", answer)
    try:
        gemini.complete("s", "p")
        assert asked == [gemini.DEFAULT_MODEL, gemini.FAST_MODEL]
        del asked[:]
        gemini.complete("s", "p")
        assert asked == [gemini.FAST_MODEL], "the busy model is not asked first"
        gemini._BUSY[gemini.DEFAULT_MODEL] = 0          # ten minutes later
        del asked[:]
        gemini.complete("s", "p")
        assert asked[0] == gemini.DEFAULT_MODEL, "and it gets its place back"
        assert gemini.DEFAULT_MODEL not in gemini._RETIRED
    finally:
        gemini._RETIRED.clear()
        gemini._BUSY.clear()


def test_a_model_that_never_answers_steps_back_too(monkeypatch):
    """A request held open past two minutes is the same answer as a 503."""
    from pinky.subs.ai import gemini

    gemini._BUSY.clear()
    asked = []

    def answer(system_prompt, prompt, timeout, name):
        asked.append((name, timeout))
        if name == gemini.DEFAULT_MODEL:
            raise gemini.GeminiError("no response from Gemini")
        return "ok"

    monkeypatch.setattr(gemini, "_complete", answer)
    try:
        assert gemini.complete("s", "p") == "ok"
        assert asked[0][1][1] <= gemini.BUSY_READ,             "with another model behind it, a minute rather than a minute and a half"
        assert gemini.DEFAULT_MODEL in gemini._BUSY
    finally:
        gemini._BUSY.clear()


def test_nothing_left_in_the_chain_is_said_once_rather_than_tried(monkeypatch):
    from pinky.subs.ai import gemini

    gemini._RETIRED.clear()
    gemini._RETIRED.update(gemini.CHAIN)
    try:
        with pytest.raises(gemini.ModelRetired):
            gemini.complete("s", "p", model_name=gemini.DEFAULT_MODEL)
    finally:
        gemini._RETIRED.clear()


def test_no_gemini_model_is_pinned_to_a_version():
    """A version number written down here is one that will be retired.

    It has happened twice: gemini-2.5-flash and then gemini-3.6-flash both
    started answering 404 for new keys while every test here passed, because
    a fixture never asks Google anything. "-latest" is Google's own answer
    and the only one that survives a release we do not make.
    """
    from pinky import settings
    from pinky.subs.ai import gemini

    names = set(gemini.CHAIN) | {gemini.DEFAULT_MODEL, gemini.FAST_MODEL,
                                 settings.DEFAULTS["subs.ai.gemini_model"]}
    for name in names:
        assert name.endswith("-latest"), "%s pins a version" % name
        assert not any(ch.isdigit() for ch in name), "%s names a version" % name


def test_the_faster_model_is_the_fallback_and_not_the_default():
    """Flash-lite has four times the daily allowance and worse Hebrew.

    Measured free tier, September 2026: flash 10/min and 250/day, flash-lite
    15/min and 1,000/day. The bigger allowance still loses, because a
    subtitle that addresses a woman as a man is what this file exists to
    avoid.
    """
    from pinky.subs.ai import gemini

    assert gemini.CHAIN[0] == gemini.DEFAULT_MODEL
    assert "lite" not in gemini.DEFAULT_MODEL
    assert "lite" in gemini.FAST_MODEL
    assert gemini.CHAIN[-1] == gemini.FAST_MODEL
    assert gemini._requests_per_minute(gemini.FAST_MODEL) > \
        gemini._requests_per_minute(gemini.DEFAULT_MODEL)


def test_a_second_engine_is_tried_when_the_first_is_out_of_quota(monkeypatch):
    """For this catalogue the translation *is* the subtitle.

    Measured over twenty-one Turkish, Korean and anime episodes: none has a
    Hebrew subtitle in existence. So an engine answering 429 does not mean a
    worse subtitle, it means none at all - and a second configured engine was
    sitting unasked while the film played blank.
    """
    from pinky.subs import srt
    from pinky.subs.ai import translator

    class Dead(object):
        def complete(self, system_prompt, prompt):
            raise translator.TranslationError("HTTP 429")

    class Alive(object):
        def complete(self, system_prompt, prompt):
            import json
            payload = json.loads(prompt.split("Input:", 1)[1].strip())
            return json.dumps({k: "שלום" for k in payload})

    dead, alive = Dead(), Alive()
    monkeypatch.setattr(translator, "engines", lambda: [dead, alive])
    cues = [srt.Cue(i + 1, i * 2.0, i * 2.0 + 1.5, "line %d" % i)
            for i in range(6)]

    out = translator.translate(cues, "he")
    assert [c.text for c in out] == ["שלום"] * 6
    assert [c.start for c in out] == [c.start for c in cues]


def test_one_engine_is_not_walked_twice(monkeypatch):
    """The common case must not become two passes over the same dead service.

    The chunk retries are its own business - a failed chunk is halved and
    tried again - but the *engine* list has one entry and must be walked once.
    """
    from pinky.subs import srt
    from pinky.subs.ai import translator

    passes = []

    class Dead(object):
        def complete(self, system_prompt, prompt):
            raise translator.TranslationError("HTTP 429")

    only = Dead()

    def engines():
        passes.append(1)
        return [only]

    monkeypatch.setattr(translator, "engines", engines)
    with pytest.raises(translator.TranslationError):
        translator.translate([srt.Cue(1, 0.0, 1.0, "line")], "he")
    assert passes == [1], "the engine list is built once per translation"


def test_a_cancelled_translation_does_not_move_to_the_next_engine(monkeypatch):
    from pinky.subs import srt
    from pinky.subs.ai import translator

    asked = []

    class Cancelled(object):
        def complete(self, system_prompt, prompt):
            asked.append("first")
            raise translator.TranslationCancelled("stopped")

    class Other(object):
        def complete(self, system_prompt, prompt):
            asked.append("second")
            return "{}"

    monkeypatch.setattr(translator, "engines", lambda: [Cancelled(), Other()])
    with pytest.raises(translator.TranslationCancelled):
        translator.translate([srt.Cue(1, 0.0, 1.0, "line")], "he")
    assert "second" not in asked, "the viewer stopped it; do not start again"


# --------------------------------------------------------------------------
# a service that will not serve us, as opposed to a chunk it cannot manage
# --------------------------------------------------------------------------


def test_an_exhausted_quota_stops_rather_than_splitting(monkeypatch):
    """Splitting is for a chunk the model could not manage. A service out of
    quota refuses the halves too, and every split doubles the requests:
    measured on The Invite, 50 became 25 became 12 became 6 over two and a
    half minutes, every one a 429, while the film played."""
    from pinky.subs.ai import translator

    calls = []

    class Exhausted(object):
        def complete(self, system_prompt, prompt):
            calls.append(prompt)
            raise RuntimeError("HTTP 429 quota exceeded")

    cues = [srt.Cue(n + 1, n * 2.0, n * 2.0 + 1.5, "line %d" % n)
            for n in range(60)]
    monkeypatch.setattr(translator, "engines", lambda: [Exhausted()])

    with pytest.raises(translator.TranslationError):
        translator.translate(cues, "he")

    assert len(calls) <= translator.MAX_RETRIES + 1, \
        "it split a doomed chunk %d times" % len(calls)


@pytest.mark.parametrize("error,refusing", [
    (RuntimeError("HTTP 429"), True),
    (RuntimeError("HTTP 503 Service Unavailable"), True),
    (RuntimeError("RESOURCE_EXHAUSTED"), True),
    (RuntimeError("reply was not JSON"), False),
    (ValueError("could not parse"), False),
])
def test_it_tells_a_refusal_from_a_difficult_chunk(error, refusing):
    from pinky.subs.ai import translator

    assert translator._service_is_refusing(error) is refusing


def test_an_unreadable_source_leaves_a_gap_rather_than_spanish(settings_module):
    """Measured on The Invite: Gemini was out of quota, the source was
    Spanish, and `Si. Gracias. Esto lo solucionara todo.` reached the screen
    in a Hebrew household. A gap says the line was not translated; Spanish
    says the add-on is broken."""
    from pinky.subs.ai import translator

    settings_module.set("subs.languages", "he,en")
    cues = [srt.Cue(1, 0.0, 1.0, "uno"), srt.Cue(2, 2.0, 3.0, "dos"),
            srt.Cue(3, 4.0, 5.0, "tres")]
    merged = translator._merge(cues, {"1": u"שתיים"}, "es")

    assert [cue.text for cue in merged] == [u"שתיים"]
    assert [cue.index for cue in merged] == [1], "the numbering has to close up"


def test_an_english_source_is_still_left_in_place(settings_module):
    """English is a language this household reads, which is the whole reason
    the rule was unconditional in the first place."""
    from pinky.subs.ai import translator

    settings_module.set("subs.languages", "he,en")
    cues = [srt.Cue(1, 0.0, 1.0, "one"), srt.Cue(2, 2.0, 3.0, "two")]
    merged = translator._merge(cues, {"1": u"שתיים"}, "en")

    assert [cue.text for cue in merged] == ["one", u"שתיים"]


def test_the_lines_being_watched_are_translated_first(use_engine, monkeypatch):
    """Hikaru no Go was nine minutes in when its translation began, and the
    first requests went on the opening scenes - already gone. From the line
    being watched, on to the end, then back for the beginning."""
    engine = use_engine("good")
    monkeypatch.setattr(translator, "engines", lambda: [engine])
    original = cues(40)                     # a line every 3 s
    seen = []
    result = translator.translate(original, "he", start_at=60.0,
                                  on_progress=lambda done, total: seen.append(done))
    firsts = [int(next(iter(json.loads(p.split("Input:", 1)[1].strip()))))
              for p in engine.prompts]
    assert firsts[0] == 20, "the line on screen at a minute"
    assert firsts[1:] == sorted(firsts[1:], key=lambda n: (n < 20, n))
    assert all(cue.text.startswith("HE:") for cue in result)
    assert seen == sorted(seen) and seen[-1] == 40, "the count only goes up"


class _Answer(object):
    def __init__(self, text, status=200):
        self.status_code = status
        self._text = text

    def json(self):
        return [[[self._text, "", None, None]]]


def test_google_is_there_when_no_key_is(settings_module):
    """Switching AI on is enough: with no key for the chosen engine, Google's
    free endpoint, which is what Kodi POV IL and DarkSubs fall back to."""
    from pinky.subs.ai import google_web

    settings_module.set_many({"subs.ai.enabled": "true", "subs.ai.gemini_key": ""})
    assert translator.engine() is google_web
    assert translator.engines()[-1] is google_web
    settings_module.set("subs.ai.enabled", "false")
    assert translator.engine() is None, "switched off is still off"


def test_google_answers_only_when_every_line_came_back(monkeypatch):
    """A merged line would put every line after it on the wrong timing."""
    from pinky.subs.ai import google_web

    sent = {}

    def post(url, data=None, **kwargs):
        sent.update(data)
        return _Answer(u"בָּטוּחַ.\nמי זה?")

    monkeypatch.setattr(google_web.http, "post", post)
    reply = json.loads(google_web.translate_lines({"7": "Sure.", "8": "Who\nis that?"}, "Hebrew"))
    assert reply == {"7": u"בטוח.", "8": u"מי זה?"}, \
        "vowel points gone, one line each"
    assert sent["tl"] == "iw" and sent["q"] == "Sure.\nWho is that?"
    assert json.loads(google_web.translate_lines({"1": "a", "2": "b", "3": "c"}, "Hebrew")) == {}


def test_an_engine_refusing_everything_hands_over_at_once(monkeypatch, settings_module):
    """Both Gemini models answered 503 to every request: each chunk waited
    out its refusals before the next engine was asked, and the episode played
    in English meanwhile."""
    settings_module.set_many({"subs.ai.enabled": "true", "subs.ai.chunk": "8"})
    refused = []

    class Busy(object):
        def complete(self, system_prompt, prompt):
            refused.append(True)
            raise RuntimeError("HTTP 503")

    good = FakeEngine("good")
    monkeypatch.setattr(translator, "engines", lambda: [Busy(), good])
    result = translator.translate(cues(40), "he")
    assert len(refused) == 1, "the first refusal, not one per chunk"
    assert all(cue.text.startswith("HE:") for cue in result)


def test_the_whole_episode_goes_to_the_fast_model_then_the_full_one_goes_over_it(
        monkeypatch, settings_module):
    """Hikaru no Go: 40 lines in Hebrew from the fast model, then English,
    because every later chunk waited on a full model that was busy all day."""
    settings_module.set_many({"subs.ai.enabled": "true", "subs.ai.chunk": "8"})

    class Model(FakeEngine):
        def __init__(self, tag, busy=False):
            FakeEngine.__init__(self)
            self.tag, self.busy = tag, busy

        def complete(self, system_prompt, prompt):
            if self.busy:
                raise RuntimeError("HTTP 503")
            reply = json.loads(FakeEngine.complete(self, system_prompt, prompt))
            return json.dumps({k: v.replace("HE:", self.tag + ":") for k, v in reply.items()})

    class Engine(object):
        def __init__(self, full_busy):
            self.fast, self.full = Model("FAST"), Model("FULL", busy=full_busy)

        def complete(self, system_prompt, prompt):
            raise AssertionError("the chain is not asked when there is a fast model")

    shown = []
    healthy = Engine(full_busy=False)
    monkeypatch.setattr(translator, "engines", lambda: [healthy])
    result = translator.translate(cues(30), "he",
                                  on_progress=lambda done, total, partial: shown.append(partial))
    assert all(cue.text.startswith("FAST:") for cue in shown[-1]), "on screen quickly"
    assert all(cue.text.startswith("FULL:") for cue in result), "and kept at its best"

    busy = Engine(full_busy=True)
    monkeypatch.setattr(translator, "engines", lambda: [busy])
    result = translator.translate(cues(30), "he")
    assert all(cue.text.startswith("FAST:") for cue in result), "a busy full model costs nothing"
