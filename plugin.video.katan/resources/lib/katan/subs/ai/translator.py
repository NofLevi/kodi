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

from ... import kodi, settings
from .. import srt

DEFAULT_CHUNK = 80
MIN_CHUNK = 8        # smallest configurable chunk size
MIN_SPLIT = 2        # smallest chunk worth splitting again
MAX_RETRIES = 2

LANGUAGE_NAMES = {
    "he": "Hebrew",
    "en": "English",
    "ar": "Arabic",
    "es": "Spanish",
    "fr": "French",
    "ru": "Russian",
}

SYSTEM_PROMPT = (
    "You are a professional subtitle translator. You translate dialogue for "
    "television and film."
)

INSTRUCTIONS = """Translate the subtitle lines below into {language}.

Rules:
- Reply with a JSON object only. No commentary, no code fences.
- Keep exactly the same keys as the input, in the same order.
- Translate every value. Never merge, split, drop or reorder entries.
- Keep each line about as short as the original so it fits on screen.
- Preserve line breaks inside a value.
- Keep names, places and brands in their usual {language} form.
- Translate the dialogue naturally rather than word by word, using the
  surrounding lines for context.

Input:
{payload}"""

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.M)


class TranslationError(Exception):
    pass


def engine():
    """The configured engine, or None when translation is switched off."""
    if not settings.get_bool("subs.ai.enabled"):
        return None
    name = settings.get("subs.ai.engine")
    if name == "gemini":
        from . import gemini
        return gemini if gemini.configured() else None
    if name == "openai":
        from . import openai_compat
        return openai_compat if openai_compat.configured() else None
    return None


def available():
    return engine() is not None


def chunk_size():
    return max(MIN_CHUNK, settings.get_int("subs.ai.chunk", DEFAULT_CHUNK))


def translate(cues, target_language="he", on_progress=None):
    """Translate cues, returning new cues with the original timings.

    on_progress(done, total) is called after each chunk so the caller can show
    partial results while the rest is still running.
    """
    backend = engine()
    if backend is None:
        raise TranslationError("no translation engine is configured")
    if not cues:
        return []

    language = LANGUAGE_NAMES.get(target_language, target_language)
    size = chunk_size()
    translated = {}
    total = len(cues)

    for start in range(0, total, size):
        batch = cues[start:start + size]
        try:
            translated.update(_translate_batch(backend, batch, language, start))
        except TranslationError:
            kodi.log_exception("chunk starting at %d failed" % start)
        if on_progress is not None:
            on_progress(min(start + size, total), total)

    if not translated:
        raise TranslationError("nothing was translated")

    out = []
    for position, cue in enumerate(cues):
        text = translated.get(str(position), "")
        out.append(srt.Cue(position + 1, cue.start, cue.end, text or cue.text))
    return out


def _translate_batch(backend, batch, language, offset, depth=0):
    """Translate one chunk, splitting it on failure rather than losing it.

    Keys are absolute positions in the file, so a split chunk still maps back
    onto the right cues.
    """
    payload = {str(offset + position): cue.text
               for position, cue in enumerate(batch)}
    prompt = INSTRUCTIONS.format(
        language=language,
        payload=json.dumps(payload, ensure_ascii=False, indent=0))

    last_error = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            reply = backend.complete(SYSTEM_PROMPT, prompt)
        except Exception as error:
            last_error = str(error)
            continue
        parsed = _parse_reply(reply)
        if parsed is None:
            last_error = "reply was not JSON"
            continue
        missing = [key for key in payload if not parsed.get(key)]
        if not missing:
            return parsed
        if len(missing) <= max(1, len(payload) // 10):
            # A couple of blanks are tolerable; the caller keeps the original.
            return parsed
        last_error = "%d of %d entries came back empty" % (len(missing), len(payload))

    if len(batch) >= MIN_SPLIT * 2 and depth < 4:
        kodi.log("splitting a failed chunk of %d (%s)" % (len(batch), last_error))
        middle = len(batch) // 2
        result = {}
        result.update(_translate_batch(backend, batch[:middle], language,
                                       offset, depth + 1))
        result.update(_translate_batch(backend, batch[middle:], language,
                                       offset + middle, depth + 1))
        return result

    raise TranslationError(last_error or "translation failed")


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
