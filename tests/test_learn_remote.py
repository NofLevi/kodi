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


def test_the_probe_leaves_the_directions_alone():
    """A box whose arrows stop working is a box nobody can answer on."""
    probe = remotekeys.probe_keymap()
    for taken in ("<up>", "<down>", "<left>", "<right>"):
        assert taken not in probe, "%s must stay bound to Kodi's own action" % taken


def test_the_probe_listens_for_ok_as_its_control():
    """The first version asked only for buttons that did not work, so
    "nothing arrived" could not tell an unreachable remote apart from a
    broken screen. OK is the button we know works - it is how anybody got
    to the screen at all."""
    probe = remotekeys.probe_keymap()
    assert "<a>" in probe and "<return>" in probe and "<select>" in probe


def test_the_probe_asks_every_input_kind_to_announce_itself():
    probe = remotekeys.probe_keymap()
    for section in ("joystick", "keyboard", "remote", "gamepad", "appcommand"):
        assert "<%s" % section in probe
    assert "action=learn&amp;key=joystick.back" in probe, "& must be XML-escaped"


def test_one_press_is_not_an_answer(settings_module, monkeypatch):
    """A remote that repeats, or a thumb that slips, must not decide."""
    monkeypatch.setattr(remotekeys, "heard",
                        lambda: [{"key": "joystick.b", "at": time.time()}])
    assert remotekeys.pressed_since(time.time() - 5) == ""


def test_the_button_pressed_most_wins(settings_module, monkeypatch):
    now = time.time()
    monkeypatch.setattr(remotekeys, "heard", lambda: [
        {"key": "keyboard.escape", "at": now},
        {"key": "joystick.b", "at": now},
        {"key": "joystick.b", "at": now},
        {"key": "joystick.b", "at": now}])
    assert remotekeys.pressed_since(now - 5) == "joystick.b"


def test_presses_from_the_last_question_do_not_answer_this_one(
        settings_module, monkeypatch):
    old = time.time() - 60
    monkeypatch.setattr(remotekeys, "heard", lambda: [
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


def test_the_joystick_section_says_which_controller_it_means():
    """Kodi reads a joystick section only when it names the controller
    profile - every example in its own joystick.xml carries the attribute.
    The first probe left it off, which is the likeliest reason the projector
    heard nothing at all. Read off a Kodi 21 install, 14 October 2026."""
    for keymap in (remotekeys.probe_keymap(),
                   remotekeys.learned_keymap({"Back": "joystick.b"})):
        assert '<joystick profile="game.controller.default">' in keymap
        assert "<joystick>" not in keymap


def test_every_name_the_probe_binds_is_one_kodi_knows():
    """A name Kodi has no feature for is a line it ignores in silence, which
    is indistinguishable from a button nobody pressed. These are the names in
    Kodi 21's own keymaps; `leftthumbbutton` is a gamepad name and was wrong
    under joystick, and appcommand has no `menu` at all."""
    known = {
        "joystick": {"a", "b", "x", "y", "start", "back", "guide", "up",
                     "down", "left", "right", "leftbumper", "rightbumper",
                     "leftthumb", "rightthumb", "lefttrigger", "righttrigger"},
        "gamepad": {"a", "b", "x", "y", "back", "start", "white", "black",
                    "leftthumbbutton", "rightthumbbutton", "lefttrigger",
                    "righttrigger", "dpadup", "dpaddown"},
        "appcommand": {"browser_back", "browser_home", "browser_stop",
                       "browser_favorites", "play", "pause", "stop"},
    }
    for section, names in remotekeys.PROBE_INPUTS:
        if section not in known:
            continue
        unknown = set(names) - known[section]
        assert not unknown, "%s has no %s" % (section, ", ".join(sorted(unknown)))


def test_the_probe_is_well_formed_xml():
    """Kodi drops a keymap it cannot parse without saying anything useful."""
    import xml.etree.ElementTree as ET
    ET.fromstring(remotekeys.probe_keymap())
    ET.fromstring(remotekeys.learned_keymap({"Back": "keyboard.escape"}))


def test_the_screen_has_nothing_on_it_that_can_be_pressed():
    """The first version used Kodi's progress dialog, which carries a Cancel
    button - so the opening instruction, "press OK", pressed Cancel and
    closed the screen. A question has to survive its own answer."""
    import os
    import xml.etree.ElementTree as ET

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    window = ET.parse(os.path.join(
        here, "plugin.video.pinky", "resources", "skins", "default", "1080i",
        "pinky-learn.xml")).getroot()
    kinds = [control.get("type") for control in window.iter("control")]
    assert "button" not in kinds, "nothing here may be pressable"
    assert "label" in kinds, "it still has to ask the question"


def test_a_press_never_closes_the_screen():
    """onAction is overridden to do nothing, which is the whole fix."""
    import inspect

    from pinky.ui import learn_remote
    source = inspect.getsource(learn_remote._Asking.onAction)
    assert "close" not in source, "a press must not end the question"
    assert "doModal" not in source


def test_a_press_really_survives_the_round_trip(settings_module):
    """Every other test here monkeypatches what was heard, so the one path
    that matters on the box - a plugin call writing a file that a window
    thread reads back - was never actually run. `record` swallows its own
    exceptions so it cannot raise into Kodi, which means a broken write
    looks exactly like a remote nobody pressed."""
    import os

    remotekeys.forget()
    moment = time.time() - 1
    remotekeys.record("joystick.b")
    remotekeys.record("joystick.b")

    assert os.path.isfile(remotekeys.heard_file()), "nothing was written down"
    assert len(remotekeys.heard()) == 2
    assert remotekeys.pressed_since(moment) == "joystick.b"

    remotekeys.forget()
    assert remotekeys.heard() == [], "forgetting has to actually forget"


def _screen():
    from pinky.ui import learn_remote
    screen = learn_remote._Asking.__new__(learn_remote._Asking)
    screen.seen = []
    return screen


def test_what_kodi_makes_of_a_press_is_evidence_on_its_own():
    """The projector answered `[7, 7, 7]` to three presses of OK while the
    keymap probe heard nothing, so the screen reported "nothing arrived" for
    a button Kodi had plainly received. What Kodi does with a press is now
    the evidence it leads with."""
    screen = _screen()
    moment = time.time()
    for _ in range(3):
        screen.seen.append({"id": 7, "at": time.time()})
    assert screen._acted_since(moment) == 7


def test_one_stray_action_decides_nothing():
    screen = _screen()
    moment = time.time()
    screen.seen.append({"id": 7, "at": time.time()})
    assert screen._acted_since(moment) is None


def test_actions_from_the_previous_question_do_not_answer_this_one():
    screen = _screen()
    old = time.time() - 60
    screen.seen += [{"id": 7, "at": old}, {"id": 7, "at": old}]
    assert screen._acted_since(time.time() - 1) is None


def test_kodi_is_asked_to_reload_the_way_kodi_understands(monkeypatch):
    """`ReloadKeymaps` is an action name, not a builtin. Called as a builtin
    it does nothing and says nothing, which is why a probe keymap written
    onto the projector was never read - there is no second "load keymapping"
    line in its log anywhere after the write. The builtin that runs an action
    by name is `Action`."""
    import xbmc

    ran = []
    monkeypatch.setattr(xbmc, "executebuiltin", lambda command: ran.append(command))
    remotekeys._reload()
    assert ran == ["Action(reloadkeymaps)"]


def test_the_ok_button_is_never_accepted_as_another_one():
    """It was, on the projector: three presses of OK arrived as one record,
    a straggler landed inside "press the button you want for Back", and the
    keymap written bound `a` to Back - which took the OK key out."""
    import inspect

    from pinky.ui import learn_remote
    source = inspect.getsource(learn_remote._Asking._ask)
    assert "self.control" in source
    assert "name = \"\"" in source, "the control answer has to be discarded"


def test_a_hold_keymap_keeps_select_working():
    """A fallback that took OK away as well would be worse than nothing."""
    keymap = remotekeys.hold_keymap("joystick.a")
    assert "<a>Select</a>" in keymap
    assert '<a holdtime="%d">Back</a>' % remotekeys.HOLD_BACK_MS in keymap
    assert '<a holdtime="%d">ContextMenu</a>' % remotekeys.HOLD_MENU_MS in keymap


def test_the_hold_keymap_names_the_controller_and_parses():
    import xml.etree.ElementTree as ET

    keymap = remotekeys.hold_keymap("joystick.a")
    ET.fromstring(keymap)
    assert '<joystick profile="game.controller.default">' in keymap


def test_a_hold_also_stops_a_film():
    keymap = remotekeys.hold_keymap("joystick.a")
    assert "<FullscreenVideo>" in keymap
    assert '<a holdtime="%d">Stop</a>' % remotekeys.HOLD_BACK_MS in keymap


def test_the_fallback_follows_whichever_button_actually_arrives():
    """`a` is this projector's OK. Another box's may not be."""
    keymap = remotekeys.hold_keymap("keyboard.escape")
    assert "<escape>Select</escape>" in keymap
    assert "<keyboard>" in keymap
