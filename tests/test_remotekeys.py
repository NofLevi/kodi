# -*- coding: utf-8 -*-
"""The remote's buttons, on a box that binds them to nothing.

Measured on a Byintek U4 projector, 9 October 2026: its remote reports to
Android as a joystick, so Kodi sees button numbers with no action behind
them - Back and Menu do nothing at all. The working keymap was read off that
box's own POV IL profile, and this ships it.
"""
import os
import xml.etree.ElementTree as ET

import pytest
import xbmc

from pinky import remotekeys


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """Kodi's keymap folder, somewhere a test may write."""
    import xbmcvfs

    folder = tmp_path / "userdata"
    folder.mkdir()
    monkeypatch.setattr(xbmcvfs, "translatePath",
                        lambda path: str(folder / "keymaps")
                        if "keymaps" in path else path)
    del xbmc.EXECUTED[:]
    return folder / "keymaps"


def test_the_shipped_keymap_binds_what_the_remote_sends():
    """Back and Menu arrive as joystick features, not as remote keys."""
    root = ET.parse(remotekeys.source()).getroot()
    joystick = root.find("./global/joystick")
    assert joystick is not None, "nothing is bound for a joystick"
    assert joystick.findtext("back") == "Back"
    assert joystick.findtext("start") == "ContextMenu"


def test_back_closes_a_film_rather_than_hiding_it():
    """Kodi's default minimises playback, which reads as a film that will
    not close."""
    root = ET.parse(remotekeys.source()).getroot()
    fullscreen = root.find("FullscreenVideo")
    assert fullscreen is not None
    for section in ("keyboard/backspace", "remote/back", "gamepad/back"):
        assert fullscreen.findtext(section) == "Stop", section


def test_back_in_menus_is_left_alone():
    """Scoped to fullscreen video: Back must still mean Back elsewhere, or
    the interface cannot be left."""
    root = ET.parse(remotekeys.source()).getroot()
    assert root.find("./global/keyboard/backspace") is None
    assert root.find("./global/remote/back") is None


def test_installing_puts_it_where_kodi_reads_keymaps(profile):
    assert not remotekeys.installed()
    assert remotekeys.install()
    assert remotekeys.installed()
    assert os.path.isfile(str(profile / remotekeys.FILE))
    ET.parse(str(profile / remotekeys.FILE))


def test_kodi_is_told_to_read_them_again(profile):
    """Keymaps are read at startup, so without this the buttons change only
    after a restart nobody was told about."""
    remotekeys.install()
    assert any("ReloadKeymaps" in command for command in xbmc.EXECUTED)


def test_it_can_be_taken_out_again(profile):
    remotekeys.install()
    del xbmc.EXECUTED[:]
    assert remotekeys.remove()
    assert not remotekeys.installed()
    assert any("ReloadKeymaps" in command for command in xbmc.EXECUTED)


def test_removing_what_was_never_there_is_not_a_failure(profile):
    assert remotekeys.remove()


def test_nothing_else_in_the_profile_is_touched(profile):
    (profile.parent / "advancedsettings.xml").write_text("<advancedsettings/>")
    remotekeys.install()
    remotekeys.remove()
    assert (profile.parent / "advancedsettings.xml").exists()


def test_every_box_in_the_house_is_covered():
    """The same press arrives as a different kind of event on each box: a
    joystick on the projector, a remote on the Mi Box, backspace on a PC,
    and an appcommand for Android's own back gesture."""
    root = ET.parse(remotekeys.source()).getroot()
    fullscreen = root.find("FullscreenVideo")
    kinds = {child.tag for child in fullscreen}
    assert {"keyboard", "remote", "gamepad", "appcommand"} <= kinds, kinds
    assert fullscreen.findtext("appcommand/browser_back") == "Stop"
    # And the projector's unbound buttons, which are joystick features.
    assert root.find("./global/joystick") is not None
