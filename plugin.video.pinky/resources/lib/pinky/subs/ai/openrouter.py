"""OpenRouter: one key, many models, several of them free.

It speaks the OpenAI protocol, so this is a preset rather than a client - an
address, a model name and the two headers OpenRouter asks applications to
send. It is here because the cost objection to translating every subtitle is
an objection to *paid* models: the free catalogue removes it, and a free model
with a large context window can be handed far bigger chunks than a paid one
would be worth.

The model is a setting rather than a constant. OpenRouter's free catalogue
changes - models arrive, are renamed and are retired - so a name hard-coded
here would be wrong by the time somebody reads this. The default is a
starting point; `openrouter.ai/models?q=free` is the current list.
"""
from ... import settings
from . import openai_compat

BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-chat-v3-0324:free"

# OpenRouter asks applications to identify themselves. Neither header carries
# anything private; they are the project, not the viewer.
HEADERS = {
    "HTTP-Referer": "https://github.com/NofLevi/kodi",
    "X-Title": "Pinky",
}


def api_key():
    return settings.get("subs.ai.openrouter_key").strip()


def model():
    return settings.get("subs.ai.openrouter_model").strip() or DEFAULT_MODEL


def configured():
    return bool(api_key())


def complete(system_prompt, prompt, timeout=openai_compat.DEFAULT_TIMEOUT):
    return openai_compat.chat(BASE, api_key(), model(), system_prompt, prompt,
                              extra_headers=HEADERS, timeout=timeout)
