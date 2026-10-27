# -*- coding: utf-8 -*-
"""Choosing the projector's remote, and it staying chosen.

Its Back key is joystick button 6 and its menu key button 7. Kodi's own
generated buttonmap named neither, and a button with no feature name produces
no action at all - which is why no keymap, and no sweep of every virtual-key
code, ever reached them.

Opt-in: a Mi Box's remote already works and must never be touched. And it has
to survive an upgrade, which the files do by living in the profile - but
`peripheral.joystick` writes that buttonmap itself and will regenerate one
without those buttons, at which point the remote silently loses its Back key
again. So the choice is remembered and checked.
"""
import os

import pytest

from pinky import remotekeys


@pytest.fixture
def box(monkeypatch, settings_module, tmp_path):
    """A box with somewhere to write both files."""
    buttonmaps = tmp_path / "buttonmaps"
    keymaps = tmp_path / "keymaps"
    buttonmaps.mkdir()
    keymaps.mkdir()
    monkeypatch.setattr(remotekeys, "buttonmap_dir", lambda: str(buttonmaps))
    monkeypatch.setattr(remotekeys, "target",
                        lambda: str(keymaps / remotekeys.FILE))
    monkeypatch.setattr(remotekeys, "_reload", lambda: None)
    return {"buttonmaps": buttonmaps, "keymaps": keymaps}


def test_the_two_buttons_kodi_could_not_see_are_named(box):
    assert remotekeys.choose_projector_remote() is True
    written = (box["buttonmaps"] / remotekeys.DEVICE_FILE).read_text("utf-8")
    assert '<feature name="back" button="6" />' in written
    assert '<feature name="start" button="7" />' in written


def test_the_dpad_is_not_also_a_thumbstick(box):
    """It arrives as buttons 18-21 *and* as the stick's axes, so every press
    moved the selection twice."""
    remotekeys.choose_projector_remote()
    written = (box["buttonmaps"] / remotekeys.DEVICE_FILE).read_text("utf-8")
    assert "leftstick" not in written
    assert '<feature name="up" button="18" />' in written


def test_back_closes_a_film_rather_than_hiding_it(box):
    remotekeys.choose_projector_remote()
    keymap = (box["keymaps"] / remotekeys.FILE).read_text("utf-8")
    assert "<back>Back</back>" in keymap
    assert "<FullscreenVideo>" in keymap
    assert "<back>Stop</back>" in keymap


def test_the_menu_key_opens_the_player_bar_during_a_film(box):
    """It opened the subtitle search instead - one item off the bar it skipped.

    Kodi binds `OSD` to `x` and `guide` in FullscreenVideo, so this is the
    action, not a window to activate; outside a film the same key is the
    context menu.
    """
    remotekeys.choose_projector_remote()
    keymap = (box["keymaps"] / remotekeys.FILE).read_text("utf-8")
    assert "SubtitleSearch" not in keymap
    assert "<start>OSD</start>" in keymap
    assert "<start>ContextMenu</start>" in keymap


def test_nothing_happens_to_a_box_that_did_not_ask(box, settings_module):
    """The whole point: a Mi Box is left exactly as it was."""
    assert remotekeys.reassert_projector_remote() is False
    assert not os.listdir(str(box["buttonmaps"]))
    assert not os.listdir(str(box["keymaps"]))


def test_a_buttonmap_regenerated_without_those_buttons_is_put_back(box):
    """peripheral.joystick writes this file itself, and the remote would
    silently lose its Back key again."""
    remotekeys.choose_projector_remote()
    (box["buttonmaps"] / remotekeys.DEVICE_FILE).write_text(
        "<buttonmap/>", encoding="utf-8")

    assert remotekeys.reassert_projector_remote() is True
    written = (box["buttonmaps"] / remotekeys.DEVICE_FILE).read_text("utf-8")
    assert '<feature name="back" button="6" />' in written


def test_a_keymap_that_went_missing_is_put_back(box):
    remotekeys.choose_projector_remote()
    os.remove(str(box["keymaps"] / remotekeys.FILE))

    assert remotekeys.reassert_projector_remote() is True
    assert (box["keymaps"] / remotekeys.FILE).is_file()


def test_an_untouched_box_is_not_rewritten_every_start(box):
    """Once chosen and intact, each start should read and do nothing."""
    remotekeys.choose_projector_remote()
    assert remotekeys.reassert_projector_remote() is False


def test_the_menu_key_opens_the_whole_description(monkeypatch):
    """On screen the plot is a 210px textbox that autoscrolls, so anything
    longer than a few lines is read a sentence at a time or not at all. The
    menu key used to open the source picker here - which is already what the
    Play button does from the same position."""
    import xbmcgui
    from pinky.ui import details_window

    window = details_window.DetailsWindow.__new__(details_window.DetailsWindow)
    window.item = {"title": "The Odyssey", "plot": "A long way home. " * 40}
    focused = []
    monkeypatch.setattr(type(window), "setFocusId",
                        lambda self, control: focused.append(control),
                        raising=False)
    del xbmcgui.Dialog.TEXTVIEWED[:]

    window._describe()

    assert xbmcgui.Dialog.TEXTVIEWED, "the plot has to be shown in full"
    heading, text = xbmcgui.Dialog.TEXTVIEWED[0]
    assert heading == "The Odyssey"
    assert text.startswith("A long way home.")
    assert focused == [details_window.BUTTON_PLAY], \
        "and it should leave the cursor on the buttons"


def test_a_film_with_no_plot_still_lands_on_the_buttons(monkeypatch):
    import xbmcgui
    from pinky.ui import details_window

    window = details_window.DetailsWindow.__new__(details_window.DetailsWindow)
    window.item = {"title": "Nameless", "plot": ""}
    focused = []
    monkeypatch.setattr(type(window), "setFocusId",
                        lambda self, control: focused.append(control),
                        raising=False)
    del xbmcgui.Dialog.TEXTVIEWED[:]

    window._describe()

    assert not xbmcgui.Dialog.TEXTVIEWED, "nothing to read, so no empty box"
    assert focused == [details_window.BUTTON_PLAY]
