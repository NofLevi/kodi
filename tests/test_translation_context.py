"""Gender context, which is what separates readable Hebrew from obvious
machine translation.

Hebrew marks the speaker's gender on verbs and adjectives, so "I am tired" has
two correct forms. English carries no such information. Telling the model who
is in the scene, and preferring to translate out of a language that already
marks gender, recovers most of what would otherwise be guesswork.
"""
import pytest

from katan.subs.ai import context


MOVIE = {
    "type": "movie",
    "ids": {"tmdb": 1},
    "item": {
        "cast": [
            {"name": "Zendaya", "role": "Chani", "gender": 1},
            {"name": "Timothee Chalamet", "role": "Paul Atreides", "gender": 2},
            {"name": "Someone", "role": "Extra", "gender": 0},
        ],
    },
}


def test_the_cast_note_names_characters_and_their_gender():
    note = context.cast_note(MOVIE)
    assert "Chani" in note and "female" in note
    assert "Paul Atreides" in note and "male" in note


def test_people_with_unknown_gender_are_left_out():
    """An unknown is worse than silence: it invites the model to guess."""
    assert "Extra" not in context.cast_note(MOVIE)


def test_no_cast_produces_no_note():
    assert context.cast_note({"type": "movie", "ids": {}}) == ""
    assert context.cast_note({}) == ""


@pytest.mark.parametrize("language,marks", [
    ("ar", True), ("es", True), ("ru", True), ("he", True), ("hi", True),
    ("en", False), ("de", False), ("nl", False), ("", False),
])
def test_gender_marking_languages_are_recognised(language, marks):
    assert bool(context.source_bonus(language)) is marks


def test_a_gender_marking_source_wins_a_close_call():
    """Spanish carries the speaker's gender; English throws it away."""
    winners = {
        "he": {"score": 30},
        "en": {"score": 70, "release": "english"},
        "es": {"score": 60, "release": "spanish"},
    }
    ranked = context.rank_translation_sources(winners, ["he", "en", "es"])
    assert ranked[0][0] == "es", "the Spanish subtitle should be preferred"


def test_a_clearly_better_match_still_wins():
    """The bonus breaks ties; it does not override a much better match."""
    winners = {
        "he": {"score": 10},
        "en": {"score": 100, "release": "english"},
        "es": {"score": 45, "release": "spanish"},
    }
    ranked = context.rank_translation_sources(winners, ["he", "en", "es"])
    assert ranked[0][0] == "en"


def test_the_target_language_is_never_a_translation_source():
    winners = {"he": {"score": 90}, "en": {"score": 50}}
    ranked = context.rank_translation_sources(winners, ["he", "en"])
    assert [language for language, _c in ranked] == ["en"]


def test_the_prompt_carries_the_cast_when_there_is_one(monkeypatch):
    from katan.subs.ai import translator

    sent = []

    class Engine(object):
        def complete(self, system_prompt, prompt):
            sent.append(prompt)
            import json
            payload = json.loads(prompt.split("Input:", 1)[1].strip())
            return json.dumps({k: "HE" for k in payload})

    monkeypatch.setattr(translator, "engine", lambda: Engine())
    from katan.subs import srt
    cues = [srt.Cue(1, 0.0, 2.0, "I am tired")]

    translator.translate(cues, "he", meta=MOVIE)
    assert "Chani" in sent[0]
    assert "female" in sent[0]
    assert "gender" in sent[0].lower()


def test_the_prompt_omits_the_cast_block_when_there_is_none(monkeypatch):
    from katan.subs.ai import translator

    sent = []

    class Engine(object):
        def complete(self, system_prompt, prompt):
            sent.append(prompt)
            import json
            payload = json.loads(prompt.split("Input:", 1)[1].strip())
            return json.dumps({k: "HE" for k in payload})

    monkeypatch.setattr(translator, "engine", lambda: Engine())
    from katan.subs import srt
    translator.translate([srt.Cue(1, 0.0, 2.0, "hello")], "he", meta=None)
    assert "The people in this scene" not in sent[0]
