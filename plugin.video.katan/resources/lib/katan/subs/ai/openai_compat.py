"""Any OpenAI-compatible chat endpoint.

This covers the hosted providers and, more usefully here, a model running on a
machine on the same network. Translating locally costs nothing per film and
keeps the subtitles off third-party servers.
"""
from ... import http, settings

DEFAULT_TIMEOUT = (10, 120)


def base_url():
    return settings.get("subs.ai.openai_url").strip().rstrip("/")


def api_key():
    return settings.get("subs.ai.openai_key").strip()


def model():
    return settings.get("subs.ai.openai_model").strip()


def configured():
    return bool(base_url() and model())


class OpenAIError(Exception):
    pass


def complete(system_prompt, prompt, timeout=DEFAULT_TIMEOUT):
    if not configured():
        raise OpenAIError("no OpenAI-compatible endpoint is configured")

    headers = {"Content-Type": "application/json"}
    if api_key():
        headers["Authorization"] = "Bearer %s" % api_key()

    body = {
        "model": model(),
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }
    response = http.post("%s/chat/completions" % base_url(), json=body,
                         headers=headers, timeout=timeout, retries=1)
    if response is None:
        raise OpenAIError("no response from the endpoint")
    if response.status_code >= 400:
        raise OpenAIError("endpoint returned HTTP %s" % response.status_code)
    try:
        payload = response.json()
    except ValueError:
        raise OpenAIError("the endpoint returned a reply that was not JSON")

    choices = payload.get("choices") or []
    if not choices:
        raise OpenAIError("the endpoint returned no choices")
    return (choices[0].get("message") or {}).get("content", "")
