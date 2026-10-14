# -*- coding: utf-8 -*-
"""The providers stopped being a question, so the answers have to hold.

Fifteen toggles left the settings screen. Two things follow. Which *working*
providers run is now the Performance profile's business alone - the shipped
defaults stay the lean profile, and Powerful is what turns Comet, MediaFusion
and Zilean on. And a provider that cannot answer at all has to be switched off
for boxes that already stored it as on, because there is no longer a screen
where anybody could do it by hand.
"""
import io
import os
import re

from pinky import profiles, settings

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_XML = os.path.join(HERE, "plugin.video.pinky", "resources",
                            "settings.xml")
PROVIDERS = re.compile(r'<setting id="((?:sources|subs)\.provider\.[^"]+)"'
                       r'.*?</setting>', re.S)


def _blocks():
    text = io.open(SETTINGS_XML, encoding="utf-8").read()
    return {m.group(1): m.group(0) for m in PROVIDERS.finditer(text)}


def test_no_provider_is_still_a_question():
    blocks = _blocks()
    assert len(blocks) == 15, "providers appeared or vanished: %s" % sorted(blocks)
    for key, block in blocks.items():
        assert "<visible>false</visible>" in block, \
            "%s is still on the settings screen" % key


def test_the_shipped_answer_is_still_the_lean_one():
    """Hiding a toggle must not quietly promote the box to a richer profile.
    Comet, MediaFusion and Zilean were switched on in a first pass at this,
    which is three more searches per play on a projector with a gigabyte of
    RAM - and `test_the_shipped_defaults_are_the_lean_profile` said so.
    """
    for key, value in profiles.LOW_MEMORY.items():
        if key.endswith(".provider." + key.rsplit(".", 1)[-1]):
            assert settings.DEFAULTS.get(key) == value, key


def test_a_dead_provider_is_switched_off_wherever_it_was_left_on(settings_module):
    """BSPlayer's hosts stopped answering and SubSource moved behind a login,
    both in September 2026. Each costs a search that cannot succeed."""
    for key in settings.RETIRED_PROVIDERS:
        settings_module.set(key, "true")

    assert settings_module.catch_up() is True
    for key in settings.RETIRED_PROVIDERS:
        assert settings_module.get(key) == "false", key


def test_it_does_not_overrule_a_chosen_profile(settings_module):
    """Powerful turns Comet on. A catch-up that turned it back off would be
    a reset wearing a different hat."""
    settings_module.set("sources.provider.comet", "true")
    settings_module.catch_up()
    assert settings_module.get("sources.provider.comet") == "true"


def test_it_runs_once(settings_module):
    assert settings_module.catch_up() is True
    settings_module.set("subs.provider.bsplayer", "true")
    assert settings_module.catch_up() is False
    assert settings_module.get("subs.provider.bsplayer") == "true"


def test_it_leaves_everything_else_alone(settings_module):
    settings_module.set("sources.max_resolution", "1080p")
    settings_module.set("subs.languages", "he")
    settings_module.catch_up()
    assert settings_module.get("sources.max_resolution") == "1080p"
    assert settings_module.get("subs.languages") == "he"
