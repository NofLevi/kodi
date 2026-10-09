# -*- coding: utf-8 -*-
"""Setting every key from a computer on the same network.

A remote is the worst keyboard in the house and this add-on is a pile of
keys, so the whole of it can be filled in from a laptop instead: one page,
every account, saved in one press.
"""
import threading
import urllib.parse
import urllib.request

import pytest

from pinky import settings, websetup


def test_every_field_is_a_setting_that_exists():
    """A field naming a setting nothing reads would silently save nowhere."""
    for key, label, note in websetup.FIELDS:
        assert key in settings.DEFAULTS, key
        assert label and note


def test_the_page_offers_every_field(settings_module):
    page = websetup.page()
    for key, label, _note in websetup.FIELDS:
        assert 'name="%s"' % key in page, key
        assert label in page


def test_a_stored_key_is_marked_but_never_printed(settings_module):
    settings_module.set("torbox.apikey", "SECRET-TORBOX-KEY")
    page = websetup.page()
    assert "SECRET-TORBOX-KEY" not in page, "the page must not hand keys out"
    assert "set" in page


def test_what_is_filled_in_is_stored(settings_module):
    changed = websetup.apply_form({"tmdb.apikey": ["abc123"],
                                   "torbox.apikey": ["tb-key"]})
    assert changed == 2
    assert settings_module.get("tmdb.apikey") == "abc123"
    assert settings_module.get("torbox.apikey") == "tb-key"


def test_an_empty_box_keeps_what_is_there(settings_module):
    settings_module.set("tmdb.apikey", "already-here")
    assert websetup.apply_form({"tmdb.apikey": [""]}) == 0
    assert settings_module.get("tmdb.apikey") == "already-here"


def test_the_same_value_again_is_not_a_change(settings_module):
    settings_module.set("tmdb.apikey", "same")
    assert websetup.apply_form({"tmdb.apikey": ["same"]}) == 0


def test_a_key_mangled_by_a_phone_keyboard_is_cleaned(settings_module):
    """iOS turns a hyphen into an en-dash in anything it reads as prose."""
    websetup.apply_form({"torbox.apikey": [u"  ab–cd  "]})
    assert settings_module.get("torbox.apikey") == "ab-cd"


def test_an_ai_key_switches_translation_on(settings_module):
    settings_module.set("subs.ai.enabled", "false")
    websetup.apply_form({"subs.ai.gemini_key": ["gem-key"]})
    assert settings_module.get("subs.ai.enabled") == "true"


def test_nothing_switches_on_without_a_key(settings_module):
    settings_module.set("subs.ai.enabled", "false")
    websetup.apply_form({"tmdb.apikey": ["only-tmdb"]})
    assert settings_module.get("subs.ai.enabled") == "false"


# --------------------------------------------------------------------------
# the server itself
# --------------------------------------------------------------------------


def _fill_in(url, fields, results):
    page = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")
    results["form"] = "<form" in page
    results["leaked"] = "SECRET-TORBOX-KEY" in page
    body = urllib.parse.urlencode(fields).encode("utf-8")
    results["reply"] = urllib.request.urlopen(url, data=body,
                                              timeout=5).read().decode("utf-8")


def test_a_page_filled_in_on_a_laptop_arrives_here(settings_module):
    settings_module.set("torbox.apikey", "SECRET-TORBOX-KEY")
    results = {}
    changed = websetup.serve(
        lifetime=15,
        on_ready=lambda url: threading.Thread(
            target=_fill_in,
            args=(url, {"tmdb.apikey": "from-the-laptop"}, results)).start())
    assert changed == 1
    assert settings_module.get("tmdb.apikey") == "from-the-laptop"
    assert results.get("form"), "no form was served"
    assert not results.get("leaked"), "a stored key was served to the browser"


def test_the_address_is_short_enough_to_type(settings_module):
    """The whole point of the short code: it can be read off a television."""
    holder = {}
    websetup.serve(lifetime=1, on_ready=lambda url: holder.setdefault("url", url))
    url = holder.get("url", "")
    assert url.startswith("http://")
    path = urllib.parse.urlsplit(url).path
    assert len(path) == 7, path          # a slash and six characters
    assert path[1:].isalnum() and path[1:].islower()


def test_it_stops_listening_once_it_is_saved(settings_module):
    holder = {}
    websetup.serve(
        lifetime=15,
        on_ready=lambda url: (holder.setdefault("url", url),
                              threading.Thread(target=_fill_in, args=(
                                  url, {"tmdb.apikey": "once"}, {})).start()))
    with pytest.raises(Exception):
        urllib.request.urlopen(holder["url"], timeout=3)


def test_giving_up_waiting_changes_nothing(settings_module):
    assert websetup.serve(lifetime=1) == 0
