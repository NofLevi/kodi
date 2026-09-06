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
    return settings.get("subs.ai.gemini_model") or "gemini-2.5-flash"


def configured():
    return bool(api_key())


def _requests_per_minute(name):
    lowered = (name or "").lower()
    for marker, limit in RATE_LIMITS.items():
        if marker in lowered:
            return limit
    return DEFAULT_RPM


def _pace():
    """Serialise requests so the whole process stays under the rate limit."""
    interval = 60.0 / float(_requests_per_minute(model()))
    with _gate:
        now = time.time()
        wait = _next_slot[0] - now
        if wait > 0:
            time.sleep(min(wait, 30.0))
            now = time.time()
        _next_slot[0] = now + interval


def complete(system_prompt, prompt, timeout=(10, 90)):
    """Send one prompt and return the text of the reply."""
    key = api_key()
    if not key:
        raise InvalidKey("no Gemini key is configured")

    _pace()
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
        },
    }
    response = http.post(
        "%s/%s:generateContent" % (BASE, model()),
        params={"key": key}, json=body, timeout=timeout, retries=1,
        headers={"Content-Type": "application/json"})

    if response is None:
        raise GeminiError("no response from Gemini")
    if response.status_code in (400, 403):
        raise InvalidKey("Gemini rejected the key (HTTP %s)" % response.status_code)
    if response.status_code == 429:
        raise GeminiError("Gemini rate limit reached")
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
    response = http.post("%s/%s:generateContent" % (BASE, model_name or model()),
                         params={"key": key}, json=body, timeout=(5, 20),
                         retries=0, headers={"Content-Type": "application/json"})
    if response is None:
        return False
    if response.status_code >= 400:
        kodi.log("Gemini key test failed with HTTP %s" % response.status_code)
        return False
    return True
