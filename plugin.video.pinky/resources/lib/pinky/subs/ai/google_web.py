"""Google Translate's free web endpoint: the engine that is always there.

Not a language model and not as good as one: it knows nothing of the cast,
so Hebrew gender is guessed, and it translates a line at a time. It is last
in the chain for that reason, and it is in the chain at all because the
alternative is English. Measured on 6 October 2026: both Gemini models
answered 503 "high demand" to every request, and Hikaru no Go played in
English with the translation retrying behind it - the same afternoon this
translated the episode's 833 lines in under five seconds. Kodi POV IL and
DarkSubs fall back to the same endpoint, which needs no key and no account.

Lines are sent many to a request, joined by newlines, and the answer is only
used when it has exactly as many lines as went in, because a merged or
dropped line would put every line after it on the wrong timing. Measured:
every batch of 40, 80 and 120 lines of that episode came back whole. A batch
that does not is refused here, and the translator splits it and asks again.
"""
import json
import re

from ... import http

ENDPOINT = "https://translate.googleapis.com/translate_a/single"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
# The translator names a language; the endpoint wants Google's code, which
# for Hebrew is still the old "iw".
CODES = {"Hebrew": "iw", "English": "en", "Arabic": "ar", "Russian": "ru",
         "French": "fr", "Spanish": "es"}
# Vowel points: Google adds them to an odd word ("בַּטוּחַ" for "Sure"), and
# no subtitle is written with them. The maqaf and the sof pasuq are
# punctuation and stay.
_POINTS = re.compile(u"[\u0591-\u05bd\u05bf\u05c1\u05c2\u05c4\u05c5\u05c7]")


class EngineUnavailable(Exception):
    """The endpoint did not answer, or refused."""


def configured():
    return True


def translate_lines(payload, language):
    """{key: text} in, the same keys translated out, as JSON text - the shape
    the translator reads from a model's reply."""
    keys = list(payload)
    lines = [" ".join(str(payload[key]).split()) for key in keys]
    response = http.post(ENDPOINT, timeout=(5, 30), retries=0,
                         data={"client": "gtx", "sl": "auto",
                               "tl": CODES.get(language, language), "dt": "t",
                               "q": "\n".join(lines)},
                         headers={"User-Agent": USER_AGENT})
    if response is None or response.status_code >= 400:
        raise EngineUnavailable("Google Translate did not answer (%s)"
                                % (getattr(response, "status_code", None) or "no response"))
    try:
        segments = response.json()[0] or []
        text = "".join(part[0] for part in segments if part and part[0])
    except (ValueError, TypeError, IndexError, KeyError):
        raise EngineUnavailable("Google Translate answered something else")
    answer = [_POINTS.sub("", line).strip() for line in text.split("\n")]
    if len(answer) != len(keys):
        # Not the whole reply: the translator treats a missing majority as a
        # chunk to split, which is exactly the fix for a merged line.
        return json.dumps({})
    return json.dumps(dict(zip(keys, answer)), ensure_ascii=False)
