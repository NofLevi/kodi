"""Engine-agnostic subtitle translation.

The design decision that matters: only the text is ever sent and only the text
comes back. Cues are keyed by index, the model fills in the values, and the
original timings are reattached untouched. Timing drift from AI translation is
therefore impossible, which is the failure mode people complain about most.

Everything else here exists to survive a model that does not follow
instructions: the reply is JSON keyed by index, the key count is checked, and a
chunk that comes back wrong is split in half and retried rather than dropped.
"""
import json
import re
import time

from ... import kodi, settings
from .. import srt

DEFAULT_CHUNK = 80
MIN_CHUNK = 8        # smallest configurable chunk size
MIN_SPLIT = 2        # smallest chunk worth splitting again
FIRST_CHUNK = 40     # lines in the first request, sent to the fastest model
MAX_RETRIES = 2
MAX_EXTRA_REQUESTS = 6
MAX_TRANSLATION_SECONDS = 10 * 60
MIN_COMPLETION = 1.0
MAX_UNCHANGED_RATIO = 0.8

LANGUAGE_NAMES = {
    "he": "Hebrew", "en": "English", "ar": "Arabic", "es": "Spanish",
    "fr": "French", "ru": "Russian", "pt": "Portuguese", "de": "German",
    "it": "Italian", "tr": "Turkish", "pl": "Polish", "zh": "Chinese",
    "ja": "Japanese", "ko": "Korean", "ro": "Romanian", "cs": "Czech",
    "uk": "Ukrainian", "hi": "Hindi",
}

SYSTEM_PROMPT = (
    "You are a professional subtitle translator. You translate dialogue for "
    "television and film."
)

INSTRUCTIONS = """Translate the subtitle lines below into {language}.
{context}
Rules:
- Reply with a JSON object only. No commentary, no code fences.
- Keep exactly the same keys as the input, in the same order.
- Translate every value. Never merge, split, drop or reorder entries.
- Keep each line about as short as the original so it fits on screen.
- Preserve line breaks inside a value.
- Keep names, places and brands in their usual {language} form.
- Translate the dialogue naturally rather than word by word, using the
  surrounding lines for context.
{linguistic_guidance}

Input:
{payload}"""

CAST_BLOCK = """
The people in this scene, for choosing the right gendered forms:
{cast}
"""

HEBREW_GUIDANCE = """- Hebrew marks the speaker's gender on verbs and adjectives. Use the cast
  list above, speaker labels and surrounding lines to choose the right form.
  When genuinely unclear, choose natural phrasing rather than defaulting to
  masculine."""

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.M)


class TranslationError(Exception):
    pass


class TranslationCancelled(TranslationError):
    pass


class TranslationBudgetExceeded(TranslationError):
    pass


ENGINE_ORDER = ("gemini", "openrouter", "openai")


def _named(name):
    """One engine module, or None when it has no credentials."""
    if name == "gemini":
        from . import gemini
        return gemini if gemini.configured() else None
    if name == "openrouter":
        from . import openrouter
        return openrouter if openrouter.configured() else None
    if name == "openai":
        from . import openai_compat
        return openai_compat if openai_compat.configured() else None
    return None


def engine():
    """The engine a translation starts on, or None when it is switched off."""
    if not settings.get_bool("subs.ai.enabled"):
        return None
    return _named(settings.get("subs.ai.engine"))


def engines():
    """Every configured engine, the chosen one first.

    The setting is a preference now rather than the whole of the answer. It
    used to be the whole of it, so an engine out of quota ended the
    translation - and for this household that means no subtitle at all rather
    than a worse one: measured over twenty-one Turkish, Korean and anime
    episodes, **none** has a Hebrew subtitle in existence. AI is not a
    fallback there, it is the only route, so Gemini answering 429 for an hour
    left a film playing with nothing on it while a second engine sat
    configured and unasked.

    Built on `engine()` rather than beside it, so there is one answer to
    "which engine is first" and not two that can disagree.

    It costs nothing to anyone with one engine, which is the common case: the
    list is one long and the loop runs once.
    """
    first = engine()
    if first is None:
        return []
    found = [first]
    for name in ENGINE_ORDER:
        module = _named(name)
        if module is not None and module not in found:
            found.append(module)
    return found


def available():
    return engine() is not None


def chunk_size():
    return max(MIN_CHUNK, settings.get_int("subs.ai.chunk", DEFAULT_CHUNK))


def translate(cues, target_language="he", on_progress=None, meta=None,
              cancelled=None):
    """Translate cues, returning new cues with the original timings.

    on_progress(done, total, cues_so_far) is called after each chunk. The third
    argument is the full cue list with everything translated so far already
    merged in, so the caller can put it on screen immediately and let the
    viewer start watching while the rest is still being translated.
    """
    backends = engines()
    if not backends:
        raise TranslationError("no translation engine is configured")
    if not cues:
        return []

    last = None
    for position, backend in enumerate(backends):
        try:
            return _translate_with(backend, cues, target_language, on_progress,
                                   meta, cancelled)
        except TranslationCancelled:
            raise
        except TranslationError as error:
            last = error
            if position + 1 < len(backends):
                # Nothing usable came back and there is another engine with
                # credentials. Measured on a real playback: Gemini answered
                # 429 for an hour and the episode played with no subtitles at
                # all, because for anime there is no Hebrew subtitle to fall
                # back to - the translation *is* the subtitle.
                kodi.log("%s could not translate this (%s), trying the next "
                         "engine" % (getattr(backend, "__name__", "engine"),
                                     str(error)[:80]))
    raise last


def _translate_with(backend, cues, target_language, on_progress, meta,
                    cancelled):
    """One engine's attempt at the whole file."""
    language = LANGUAGE_NAMES.get(target_language, target_language)
    context = _context_block(meta)
    size = chunk_size()
    translated = {}
    total = len(cues)
    # A short first chunk on the engine's fastest model, so the first Hebrew
    # line is on screen in seconds rather than after a full chunk. Measured on
    # Gemini: 40 lines on the lite model in 2.5 s, against 43 s for the first
    # 100 on the full one. Every later chunk arrives well before the film
    # reaches it, so only the first is worth hurrying.
    first = min(FIRST_CHUNK, size, total)
    ranges = [(0, first)] + [(start, min(start + size, total))
                             for start in range(first, total, size)]
    base_requests = len(ranges)
    budget = {
        "calls": 0,
        # Room to split, which has to grow a little with the file: a failed
        # chunk is halved and retried, and six spare calls is under one split
        # when a film is a hundred chunks. Half the chunk count rather than
        # all of it, because the point of the cap is that one failing model
        # cannot turn a film into hundreds of requests.
        "max_calls": (base_requests
                      + (1 if hasattr(backend, "fast") else 0)   # first chunk, redone
                      + max(MAX_EXTRA_REQUESTS, base_requests // 2)),
        "deadline": time.monotonic() + MAX_TRANSLATION_SECONDS,
        "cancelled": cancelled or (lambda: False),
    }

    for index, (start, end) in enumerate(ranges):
        if budget["cancelled"]():
            raise TranslationCancelled("translation cancelled")
        batch = cues[start:end]
        engine_for_chunk = getattr(backend, "fast", backend) if index == 0 else backend
        try:
            translated.update(
                _translate_batch(engine_for_chunk, batch, language, start,
                                 context, budget=budget))
        except (TranslationCancelled, TranslationBudgetExceeded):
            raise
        except TranslationError:
            kodi.log_exception("chunk starting at %d failed" % start)
        if on_progress is not None:
            try:
                on_progress(end, total, _merge(cues, translated))
            except TypeError:
                # Callers that only want the counts.
                on_progress(end, total)
            except Exception:
                kodi.log_exception("progress callback failed")

    # The first chunk went to the fast model so the film had words on it in
    # seconds, and it pays for that: measured, the lite model made a male
    # speaker female ("אני יכולה") where the full model did not. Those lines
    # have long since been read by now, but the file is kept and watched
    # again, so they are redone on the full model - one request - and only a
    # better answer replaces the quick one.
    fast = getattr(backend, "fast", None)
    if fast is not None and first and not budget["cancelled"]():
        try:
            translated.update(_translate_batch(
                backend, cues[:first], language, 0, context, budget=budget))
        except (TranslationCancelled, TranslationBudgetExceeded):
            pass
        except TranslationError:
            kodi.log("kept the quick translation of the first %d lines" % first)

    completed = sum(1 for position in range(total)
                    if translated.get(str(position)))
    if not completed:
        raise TranslationError("nothing was translated")
    if float(completed) / total < MIN_COMPLETION:
        raise TranslationError("only %d of %d cues were translated" %
                               (completed, total))
    if budget["cancelled"]():
        raise TranslationCancelled("translation cancelled")

    result = _merge(cues, translated)
    unchanged = sum(1 for source, target in zip(cues, result)
                    if " ".join(source.text.split()) == " ".join(target.text.split()))
    if float(unchanged) / total >= MAX_UNCHANGED_RATIO:
        raise TranslationError("translation repeated the source text")
    return result


def _merge(cues, translated):
    """Overlay the translated text onto the original cues.

    Untranslated positions keep their original text, so a partial result is a
    playable subtitle rather than a file full of gaps.
    """
    out = []
    for position, cue in enumerate(cues):
        text = translated.get(str(position), "")
        out.append(srt.Cue(position + 1, cue.start, cue.end, text or cue.text))
    return out


def _context_block(meta):
    """The cast note, when there is one worth sending."""
    if not meta:
        return ""
    try:
        from . import context as translation_context
        note = translation_context.cast_note(meta)
    except Exception:
        kodi.log_exception("could not build the translation context")
        return ""
    return CAST_BLOCK.format(cast=note) if note else ""


def _translate_batch(backend, batch, language, offset, context="", depth=0,
                     budget=None):
    """Translate one chunk, splitting it on failure rather than losing it.

    Keys are absolute positions in the file, so a split chunk still maps back
    onto the right cues.
    """
    payload = {str(offset + position): cue.text
               for position, cue in enumerate(batch)}
    prompt = INSTRUCTIONS.format(
        language=language, context=context,
        linguistic_guidance=(HEBREW_GUIDANCE if language == "Hebrew" else ""),
        payload=json.dumps(payload, ensure_ascii=False, indent=0))

    last_error = ""
    for attempt in range(MAX_RETRIES + 1):
        if budget is not None:
            if budget["cancelled"]():
                raise TranslationCancelled("translation cancelled")
            if (budget["calls"] >= budget["max_calls"]
                    or time.monotonic() >= budget["deadline"]):
                raise TranslationBudgetExceeded("translation request budget exhausted")
            budget["calls"] += 1
        try:
            reply = backend.complete(SYSTEM_PROMPT, prompt)
            if budget is not None and budget["cancelled"]():
                raise TranslationCancelled("translation cancelled")
        except (TranslationCancelled, TranslationBudgetExceeded):
            raise
        except Exception as error:
            # Backend exceptions may embed Authorization headers, API keys, or
            # signed endpoint URLs. The class is enough to explain retry/split.
            last_error = type(error).__name__
            continue
        parsed = _parse_reply(reply)
        if parsed is None:
            last_error = "reply was not JSON"
            continue
        missing = [key for key in payload if not parsed.get(key)]
        expected = {key: parsed[key] for key in payload if parsed.get(key)}
        if not missing:
            return expected
        if len(missing) <= max(1, len(payload) // 10):
            # A line or two dropped from an otherwise good reply. Ask again
            # for exactly those lines, because translate() refuses a film
            # with even one line missing - measured: a 300-line chunk came
            # back 299 of 300 and the whole translation was thrown away.
            # Re-sending the whole chunk would pay for 299 lines twice.
            if depth < 4:
                expected.update(_retry_missing(
                    backend, batch, language, offset, context, missing,
                    depth, budget))
            return expected
        last_error = "%d of %d entries came back empty" % (len(missing), len(payload))
        # A structurally valid partial reply usually means the batch is too
        # large or difficult. Repeating it unchanged wastes requests; split now.
        break

    if len(batch) >= MIN_SPLIT * 2 and depth < 4:
        kodi.log("splitting a failed chunk of %d (%s)" % (len(batch), last_error))
        middle = len(batch) // 2
        result = {}
        result.update(_translate_batch(backend, batch[:middle], language,
                                       offset, context, depth + 1, budget))
        result.update(_translate_batch(backend, batch[middle:], language,
                                       offset + middle, context, depth + 1,
                                       budget))
        return result

    raise TranslationError(last_error or "translation failed")


def _retry_missing(backend, batch, language, offset, context, missing, depth,
                   budget):
    """Translate only the lines a reply left out, one run of them at a time."""
    positions = sorted(int(key) - offset for key in missing)
    runs, run = [], [positions[0]]
    for position in positions[1:]:
        if position == run[-1] + 1:
            run.append(position)
        else:
            runs.append(run)
            run = [position]
    runs.append(run)
    recovered = {}
    for run in runs:
        try:
            recovered.update(_translate_batch(
                backend, batch[run[0]:run[-1] + 1], language, offset + run[0],
                context, depth + 1, budget))
        except (TranslationCancelled, TranslationBudgetExceeded):
            raise
        except TranslationError:
            kodi.log("could not recover %d dropped line(s)" % len(run))
    return recovered


def _parse_reply(text):
    """Read the model reply as a flat {key: string} mapping."""
    if not text:
        return None
    cleaned = _FENCE.sub("", text).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return {str(key): _as_text(value) for key, value in data.items()}


def _as_text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(str(part) for part in value).strip()
    return str(value).strip() if value is not None else ""
