# -*- coding: utf-8 -*-
"""OpenRouter: the OpenAI protocol, one key, and a catalogue of free models.

The cost objection to translating subtitles is an objection to paid models.
This is a preset over the OpenAI-compatible client rather than a second one,
so what is worth testing is the preset, and the two ways a free model answers
that a paid one does not.
"""
import pytest

from katan import http
from katan.subs.ai import openai_compat, openrouter, translator


class Reply(object):
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {
            "choices": [{"message": {"content": '{"0": "\u05e9\u05dc\u05d5\u05dd"}'}}]}

    def json(self):
        return self._payload


@pytest.fixture
def posted(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None, retries=0):
        calls.append({"url": url, "body": json, "headers": headers})
        return calls[-1].get("reply") or Reply()

    monkeypatch.setattr(http, "post", fake_post)
    return calls


def test_the_key_is_all_it_needs(settings_module):
    settings_module.set("subs.ai.openrouter_key", "")
    assert openrouter.configured() is False
    settings_module.set("subs.ai.openrouter_key", "sk-or-v1-x")
    assert openrouter.configured() is True


def test_the_engine_is_selected_by_the_setting(settings_module):
    settings_module.set_many({"subs.ai.enabled": "true",
                              "subs.ai.engine": "openrouter",
                              "subs.ai.openrouter_key": "sk-or-v1-x"})
    assert translator.engine() is openrouter
    assert translator.available() is True


def test_it_talks_to_openrouter_and_says_who_it_is(posted, settings_module):
    settings_module.set_many({"subs.ai.openrouter_key": "sk-or-v1-x",
                              "subs.ai.openrouter_model": "some/model:free"})
    openrouter.complete("system", "prompt")
    call = posted[0]
    assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-or-v1-x"
    assert call["headers"]["X-Title"] == "Katan"
    assert call["body"]["model"] == "some/model:free"


def test_a_model_that_refuses_json_mode_is_asked_again_without_it(
        monkeypatch, settings_module):
    """Several free models answer HTTP 400 to a request that carries
    response_format at all. Losing them to a flag we do not need would throw
    away the catalogue this exists for; the reply is checked for JSON anyway."""
    settings_module.set("subs.ai.openrouter_key", "sk-or-v1-x")
    seen = []

    def fake_post(url, json=None, headers=None, timeout=None, retries=0):
        seen.append("response_format" in json)
        return Reply(400) if seen[-1] else Reply()

    monkeypatch.setattr(http, "post", fake_post)
    assert "\u05e9\u05dc\u05d5\u05dd" in openrouter.complete("system", "prompt")
    assert seen == [True, False]


def test_an_error_delivered_as_a_200_is_not_an_empty_translation(
        monkeypatch, settings_module):
    """OpenRouter reports an exhausted free model as a 200 with an error
    object, which would otherwise read as a subtitle with nothing in it."""
    settings_module.set("subs.ai.openrouter_key", "sk-or-v1-x")
    monkeypatch.setattr(http, "post",
                        lambda *a, **k: Reply(200, {"error": {"code": 429}}))
    with pytest.raises(openai_compat.OpenAIError):
        openrouter.complete("system", "prompt")


def test_the_model_is_a_setting_because_free_models_come_and_go(settings_module):
    settings_module.set("subs.ai.openrouter_model", "")
    assert openrouter.model() == openrouter.DEFAULT_MODEL
    settings_module.set("subs.ai.openrouter_model", "qwen/qwen3:free")
    assert openrouter.model() == "qwen/qwen3:free"
