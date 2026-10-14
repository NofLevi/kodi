# -*- coding: utf-8 -*-
"""Learning a remote instead of guessing at it.

The shipped keymap bound the joystick features `back` and `start`, which
Kodi's own keymap already binds - so on the Byintek projector it changed
nothing twice over. These cover the half that can be checked without the
remote in hand: that the probe never takes navigation away, that a single
stray press decides nothing, and that what is written out is what was heard.
"""
import time

import pytest

from pinky import remotekeys


def test_the_probe_leaves_navigation_alone():
    """A box whose arrows stop working is a box nobody can answer on."""
    probe = remotekeys.probe_keymap()
    for taken in ("<up>", "<down>", "<left>", "<right>", "<a>", "<select>",
                  "<return>", "<enter>"):
        assert taken not in probe, "%s must stay bound to Kodi's own action" % taken


def test_the_probe_asks_every_input_kind_to_announce_itself():
    probe = remotekeys.probe_keymap()
    for section in ("joystick", "keyboard", "remote", "gamepad", "appcommand"):
        assert "<%s>" % section in probe
    assert "action=learn&amp;key=joystick.back" in probe, "& must be XML-escaped"


def test_one_press_is_not_an_answer(settings_module, monkeypatch):
    """A remote that repeats, or a thumb that slips, must not decide."""
    monkeypatch.setattr(remotekeys, "_heard",
                        lambda: [{"key": "joystick.b", "at": time.time()}])
    assert remotekeys.pressed_since(time.time() - 5) == ""


def test_the_button_pressed_most_wins(settings_module, monkeypatch):
    now = time.time()
    monkeypatch.setattr(remotekeys, "_heard", lambda: [
        {"key": "keyboard.escape", "at": now},
        {"key": "joystick.b", "at": now},
        {"key": "joystick.b", "at": now},
        {"key": "joystick.b", "at": now}])
    assert remotekeys.pressed_since(now - 5) == "joystick.b"


def test_presses_from_the_last_question_do_not_answer_this_one(
        settings_module, monkeypatch):
    old = time.time() - 60
    monkeypatch.setattr(remotekeys, "_heard", lambda: [
        {"key": "joystick.b", "at": old}, {"key": "joystick.b", "at": old}])
    assert remotekeys.pressed_since(time.time() - 5) == ""


def test_what_was_heard_is_what_gets_bound():
    keymap = remotekeys.learned_keymap({"Back": "keyboard.escape",
                                        "ContextMenu": "remote.title"})
    assert "<escape>Back</escape>" in keymap
    assert "<title>ContextMenu</title>" in keymap
    assert "<keyboard>" in keymap and "<remote>" in keymap


def test_back_also_stops_a_film():
    """Kodi's own Back during playback only minimises it, which on a
    projector reads as a film that will not close."""
    keymap = remotekeys.learned_keymap({"Back": "keyboard.escape"})
    assert "<FullscreenVideo>" in keymap
    assert "<escape>Stop</escape>" in keymap


def test_a_remote_that_said_nothing_binds_nothing():
    keymap = remotekeys.learned_keymap({})
    assert "<FullscreenVideo>" not in keymap
    assert keymap.strip().startswith("<keymap>")
