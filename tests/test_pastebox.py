# -*- coding: utf-8 -*-
"""Getting a key onto the box without typing it on a remote.

A TorBox key is thirty-two characters and an on-screen keyboard driven by a
remote is about a minute of work to get it wrong. Kodi shows a LAN address,
you open it on the phone that already has the key in its clipboard, and it
arrives here. The idea is taken from the Kodi POV IL build.
"""
import threading
import time
import urllib.parse
import urllib.request

import pytest

from katan import pastebox


# --------------------------------------------------------------------------
# what a phone keyboard does to a pasted key
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pasted,expected", [
    ("  94584cab08f71b624286f19eda8d2b5e  ", "94584cab08f71b624286f19eda8d2b5e"),
    ("\n94584cab\n", "94584cab"),
    (u"\u201c94584cab\u201d", "94584cab"),          # iOS smart quotes
    (u"abc\u2013def", "abc-def"),                    # en-dash for a hyphen
    (u"abc\u2014def", "abc-def"),                    # em-dash
    (u"\uff41\uff42\uff43", "abc"),                  # full-width letters
    ("key_with-safe.chars:1/2+3=", "key_with-safe.chars:1/2+3="),
    ("", ""),
    (None, ""),
])
def test_a_pasted_key_is_cleaned_up(pasted, expected):
    """iOS substitutes smart quotes and en-dashes into anything it thinks is
    prose, and a key with a U+2013 in it fails authentication with no clue."""
    assert pastebox.sanitise(pasted) == expected


def test_a_key_is_never_silently_mangled_into_something_shorter():
    """The allow-list must keep every character a real key uses."""
    key = "eyJhbGciOiJIUzI1NiJ9.abc-DEF_123.xyz"
    assert pastebox.sanitise(key) == key


# --------------------------------------------------------------------------
# the page itself
# --------------------------------------------------------------------------


def test_it_finds_an_address_the_phone_can_reach():
    """Reading the hostname returns 127.0.0.1 as often as anything useful."""
    address = pastebox.lan_address()
    assert address
    assert not address.startswith("127."), "a phone cannot reach loopback"


def _submit(url, value, results):
    try:
        page = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")
        results["form"] = '<form' in page and 'name="value"' in page
        body = urllib.parse.urlencode({"value": value}).encode()
        results["reply"] = urllib.request.urlopen(url, data=body,
                                                  timeout=5).read().decode("utf-8")
    except Exception as error:      # pragma: no cover - a failure is the report
        results["error"] = "%s: %s" % (type(error).__name__, error)


def test_a_key_pasted_on_the_page_arrives_here():
    results = {}
    value = pastebox.receive(
        "TorBox API key", lifetime=15,
        on_ready=lambda url: threading.Thread(
            target=_submit, args=(url, " 94584cab08f71b624286f19eda8d2b5e ",
                                  results)).start())

    assert not results.get("error"), results.get("error")
    assert results.get("form"), "the page did not serve a form"
    assert value == "94584cab08f71b624286f19eda8d2b5e"


def test_the_page_stops_listening_once_it_has_the_key():
    """One page, one field, one submission. A forgotten dialog must not leave
    a socket open on the network all evening."""
    holder = {}
    pastebox.receive("Key", lifetime=15,
                     on_ready=lambda url: holder.setdefault("url", url) or
                     threading.Thread(target=_submit,
                                      args=(url, "abc123", {})).start())
    time.sleep(0.3)
    with pytest.raises(Exception):
        urllib.request.urlopen(holder["url"], timeout=3)


def test_giving_up_waiting_returns_nothing_rather_than_hanging():
    started = time.time()
    assert pastebox.receive("Key", lifetime=1) == ""
    assert time.time() - started < 6, "the lifetime did not end the wait"


def test_an_empty_paste_does_not_count_as_an_answer():
    """Somebody pressing Send on an empty box has not finished."""
    results = {}
    value = pastebox.receive(
        "Key", lifetime=2,
        on_ready=lambda url: threading.Thread(
            target=_submit, args=(url, "   ", results)).start())
    assert value == ""
