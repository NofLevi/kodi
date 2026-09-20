"""Any OpenAI-compatible chat endpoint.

This covers the hosted providers and, more usefully here, a model running on a
machine on the same network. Translating locally costs nothing per film and
keeps the subtitles off third-party servers.
"""
from ... import http, kodi, settings

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
    return chat(base_url(), api_key(), model(), system_prompt, prompt,
                timeout=timeout)


def chat(endpoint, key, name, system_prompt, prompt, extra_headers=None,
         timeout=DEFAULT_TIMEOUT):
    """One chat completion against any OpenAI-compatible endpoint.

    Split out so OpenRouter - which is this protocol with its own address and
    a catalogue of free models - is a preset rather than a second client.

    `response_format` is asked for and then given up on: the hosted models
    honour it, and several of the free ones answer **HTTP 400** to a request
    that carries it at all. Losing every one of those to a flag we do not need
    would be the whole point of the free catalogue thrown away - the reply is
    checked for JSON either way, and `_parse_reply` already strips code fences
    and prose from models that answer in neither.
    """
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer %s" % key
    headers.update(extra_headers or {})

    body = {
        "model": name,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }
    response = http.post("%s/chat/completions" % endpoint.rstrip("/"),
                         json=body, headers=headers, timeout=timeout,
                         retries=1)
    if response is not None and response.status_code == 400:
        body.pop("response_format", None)
        kodi.log("the model refused a JSON-mode request; asking without it")
        response = http.post("%s/chat/completions" % endpoint.rstrip("/"),
                             json=body, headers=headers, timeout=timeout,
                             retries=1)
    if response is None:
        raise OpenAIError("no response from the endpoint")
    if response.status_code >= 400:
        raise OpenAIError("endpoint returned HTTP %s" % response.status_code)
    try:
        payload = response.json()
    except ValueError:
        raise OpenAIError("the endpoint returned a reply that was not JSON")

    # OpenRouter reports a refused or exhausted free model as a 200 with an
    # error object, which would otherwise read as an empty translation.
    if payload.get("error"):
        raise OpenAIError("the endpoint refused the request")
    choices = payload.get("choices") or []
    if not choices:
        raise OpenAIError("the endpoint returned no choices")
    return (choices[0].get("message") or {}).get("content", "")
