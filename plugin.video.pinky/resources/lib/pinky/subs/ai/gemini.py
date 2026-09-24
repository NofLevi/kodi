"""Google Gemini.

Chosen as the default because the free tier is generous enough to translate a
film or a few episodes a day without a credit card, which suits a home setup.

Requests are paced to the model's requests-per-minute limit, because hitting
the limit mid-film produces a half translated subtitle, which is worse than a
slower one.
"""
import threading
import time

from ... import http, kodi, settings

BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Aliases, never a version number. Google retires versions for new users and
# does it silently: on 22 September 2026 gemini-2.5-flash and
# gemini-2.5-flash-lite answered every request from a new key with HTTP 404,
# which made AI translation fail for anyone with a new key while every test
# here passed - and then a pinned gemini-3.6-flash went the same way for a
# real key two days later. A name written down here is a name that will be
# wrong; "-latest" is Google's own answer to that and costs nothing.
#
# Flash first and flash-lite behind it, which is the quality order rather
# than the allowance order. Measured free tier, September 2026: flash is
# 10 requests a minute and 250 a day, flash-lite is 15 and 1,000 - so
# flash-lite is four times the daily allowance and half again the speed, and
# is still second, because it was measured to get Hebrew gender wrong where
# flash gets it right and a subtitle that addresses a woman as a man is the
# thing this whole file exists to avoid. They are separate quotas, so a
# spent flash is not the end of the film.
#
# A model that is retired (404) is struck off for the session; overloaded
# (503) or out of quota (429) keeps its place and is tried again.
# gemini-pro-latest is never in the chain: Pro left the free tier in May 2026
# and answers 429.
DEFAULT_MODEL = "gemini-flash-latest"
FAST_MODEL = "gemini-flash-lite-latest"
CHAIN = (DEFAULT_MODEL, FAST_MODEL)

# Conservative pacing per model family, a little under the published limits.
RATE_LIMITS = {
    "flash-lite": 14,
    "flash": 9,
    "pro": 4,
}
DEFAULT_RPM = 6

_gate = threading.Lock()
_next_slot = [0.0]


class InvalidKey(Exception):
    pass


class GeminiError(Exception):
    pass


def api_key():
    return settings.get("subs.ai.gemini_key").strip()


def model():
    return settings.get("subs.ai.gemini_model").strip() or DEFAULT_MODEL


def configured():
    return bool(api_key())


def _requests_per_minute(name):
    lowered = (name or "").lower()
    for marker, limit in RATE_LIMITS.items():
        if marker in lowered:
            return limit
    return DEFAULT_RPM


def _pace(name=None):
    """Serialise requests so the whole process stays under the rate limit."""
    interval = 60.0 / float(_requests_per_minute(name or model()))
    with _gate:
        now = time.time()
        wait = _next_slot[0] - now
        if wait > 0:
            time.sleep(min(wait, 30.0))
            now = time.time()
        _next_slot[0] = now + interval


def complete(system_prompt, prompt, timeout=(10, 90), model_name=None):
    """Send one prompt and return the text of the reply.

    Tries the requested model and then the rest of CHAIN, stopping at the
    first that answers. Only for a model that cannot serve the request -
    retired, overloaded, out of quota. A bad key or a bad reply is not the
    model's fault and is raised as it is.
    """
    first = model_name or model()
    names = [name for name in [first] + [n for n in CHAIN if n != first]
             if name not in _RETIRED]
    if not names:
        raise ModelRetired("no Gemini model in the chain is served for this key")
    last = None
    for name in names:
        try:
            return _complete(system_prompt, prompt, timeout, name)
        except ModelRetired as error:
            last = error
            _RETIRED.add(name)
            kodi.log("Gemini model %s is not served here, so it will not be "
                     "asked again (%s)" % (name, error))
        except ModelUnavailable as error:
            last = error
            kodi.log("Gemini model %s could not take the request (%s)"
                     % (name, error))
    raise last


class _Fast(object):
    """The same engine on the quickest model, for the first chunk of a film.

    Measured: the lite model returned 40 lines in 2.5 s where the full model
    took 43 s for its first 100 - and nothing is on screen until the first
    chunk is back. The rest of the film goes to the better model, which gets
    the Hebrew gender right where the lite one did not.
    """

    def complete(self, system_prompt, prompt, timeout=(10, 90)):
        return complete(system_prompt, prompt, timeout, model_name=FAST_MODEL)


fast = _Fast()


class ModelUnavailable(GeminiError):
    """This model cannot take the request now: overloaded or out of quota."""


class ModelRetired(ModelUnavailable):
    """This model is not served here at all, and will not be on the next try."""


# Models this process has been told do not exist. A 404 is the API saying the
# name is not served on this endpoint - retired, or never offered for this
# key's API version - so it will answer 404 again for every chunk of the
# film. Measured on a real translation: the configured model answered 404 and
# was asked again for every chunk, three requests and two retries deep each
# time, for over a minute of a film that was already playing. A 429 or a 503
# is the opposite claim - the model is there and busy - so it keeps its place
# in the chain and is tried again.
_RETIRED = set()


def _complete(system_prompt, prompt, timeout, name):
    key = api_key()
    if not key:
        raise InvalidKey("no Gemini key is configured")

    _pace(name)
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
        },
    }
    response = http.post(
        "%s/%s:generateContent" % (BASE, name),
        params={"key": key}, json=body, timeout=timeout, retries=1,
        headers={"Content-Type": "application/json"})

    if response is None:
        raise GeminiError("no response from Gemini")
    if response.status_code == 404:
        raise ModelRetired("HTTP 404")
    if response.status_code in (429, 503):
        raise ModelUnavailable("HTTP %s" % response.status_code)
    if response.status_code in (400, 403):
        raise InvalidKey("Gemini rejected the key (HTTP %s)" % response.status_code)
    if response.status_code >= 400:
        raise GeminiError("Gemini returned HTTP %s" % response.status_code)

    try:
        payload = response.json()
    except ValueError:
        raise GeminiError("Gemini returned a reply that was not JSON")

    return _text_of(payload)


def _text_of(payload):
    candidates = payload.get("candidates") or []
    if not candidates:
        blocked = (payload.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            raise GeminiError("Gemini refused the content: %s" % blocked)
        raise GeminiError("Gemini returned no candidates")
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    return "".join(part.get("text", "") for part in parts)


def test_key(key=None, model_name=None):
    """A tiny request used by the setup wizard to confirm a key works."""
    key = (key or api_key()).strip()
    if not key:
        return False
    body = {
        "contents": [{"role": "user", "parts": [{"text": "Reply with: ok"}]}],
        "generationConfig": {"maxOutputTokens": 8},
    }
    # The fast alias, not the configured model: a retired model answers 404,
    # and reporting that as "the key is invalid" sent people to make a new key
    # that failed in exactly the same way.
    response = http.post("%s/%s:generateContent" % (BASE, model_name or FAST_MODEL),
                         params={"key": key}, json=body, timeout=(5, 20),
                         retries=0, headers={"Content-Type": "application/json"})
    if response is None:
        return False
    if response.status_code >= 400:
        kodi.log("Gemini key test failed with HTTP %s" % response.status_code)
        return False
    return True
