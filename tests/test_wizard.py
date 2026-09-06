"""First-run setup.

The step tested here is the one that is not an account: the choice between
light and richer artwork. It exists because the add-on ships lean for a 1 GB
projector, and somebody running it on better hardware should be able to say so
without going looking for a settings page.
"""
import pytest

from katan import kodi, profiles, settings
from katan.ui import wizard


@pytest.fixture
def chose(monkeypatch):
    """Answer the selection dialog, and hand back what it was asked."""
    seen = {}

    def install(index):
        def fake_select(options, heading=None, preselect=-1, use_details=False):
            seen["options"] = list(options)
            seen["heading"] = heading
            seen["preselect"] = preselect
            return index
        monkeypatch.setattr(kodi, "select", fake_select)
        return seen

    return install


def test_the_steps_include_picture_quality(monkeypatch):
    """It is a decision about how the add-on looks, so it is not buried."""
    captured = {}

    def fake_select(options, heading=None, preselect=-1, use_details=False):
        captured["options"] = list(options)
        return -1

    monkeypatch.setattr(kodi, "select", fake_select)
    monkeypatch.setattr(wizard, "_finish", lambda open_home_after=True: None)
    wizard.run(open_home_after=False)

    assert any(kodi.localize(32410) in label for label in captured["options"])


def test_choosing_richer_artwork_applies_it(chose):
    profiles.apply("low_memory")
    chose(1)
    wizard.step_visuals()

    assert settings.get_bool("ui.rich_visuals") is True
    assert settings.get("ui.poster_size") == "w342"


def test_choosing_light_leaves_the_lean_settings(chose):
    settings.set("ui.rich_visuals", "true")
    profiles.apply("low_memory")
    chose(0)
    wizard.step_visuals()

    assert settings.get_bool("ui.rich_visuals") is False
    assert settings.get("ui.poster_size") == "w185"


def test_cancelling_changes_nothing(chose):
    profiles.apply("low_memory")
    chose(-1)
    wizard.step_visuals()
    assert settings.get("ui.poster_size") == "w185"


def test_both_choices_say_what_they_cost(chose):
    """The memory number is the reason the lean profile exists, so it is
    shown rather than left for the viewer to discover."""
    seen = chose(-1)
    wizard.step_visuals()

    light, rich = seen["options"]
    assert "w185" in light and "w342" in rich
    assert "MB" in light and "MB" in rich


def test_the_current_answer_is_the_one_highlighted(chose):
    settings.set("ui.rich_visuals", "true")
    seen = chose(-1)
    wizard.step_visuals()
    assert seen["preselect"] == 1

    settings.set("ui.rich_visuals", "false")
    seen = chose(-1)
    wizard.step_visuals()
    assert seen["preselect"] == 0


def test_the_device_gets_an_opinion_but_not_a_decision(chose, monkeypatch):
    """A capable box is told it has room. It is not quietly switched."""
    monkeypatch.setattr(profiles, "_free_megabytes", lambda: 4000)
    monkeypatch.setattr(profiles, "_core_count", lambda: 8)

    seen = chose(-1)
    wizard.step_visuals()

    assert "4000" in seen["heading"], "the reason should reach the heading"
    assert settings.get_bool("ui.rich_visuals") is False, \
        "a recommendation is not a change"
