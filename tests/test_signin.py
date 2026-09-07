# -*- coding: utf-8 -*-
"""The one sign-in screen every service shares.

Signing in is the flow with the least margin for error in the whole add-on:
it runs once, it runs while somebody is holding a phone, and when it goes
wrong the symptom is a screen that sits there. So the parts that decide what
happens - which methods are offered, what a poll answer means, and what is
kept when a new key does not work - are tested rather than watched.
"""
import pytest

from katan.ui import signin


class FakeClient(object):
    """Stands in for a debrid client, recording what it was asked to do."""

    name = "fake"
    label = "Fake"
    methods = ("scan", "link", "key")
    key_url = "https://example.test/key"

    def __init__(self):
        self.called_with = []
        self.ok = True

    def authorize(self, method=None):
        self.called_with.append(method)
        return self.ok


# --------------------------------------------------------------------------
# which methods are offered
# --------------------------------------------------------------------------


def test_the_methods_are_offered_best_first(monkeypatch):
    """Scan first, because it is the one with nothing to type."""
    shown = {}
    monkeypatch.setattr(signin.kodi, "select",
                        lambda labels, heading="", **kw:
                            shown.setdefault("labels", labels) and 0 or 0)
    signin.choose_method("Fake", ("key", "link", "scan"))
    labels = shown["labels"]
    assert labels == [signin.kodi.localize(32460), signin.kodi.localize(32461),
                      signin.kodi.localize(32462)]


def test_a_service_with_one_way_in_is_not_asked(monkeypatch):
    """TorBox has a key and nothing else. A one-item menu is a speed bump."""
    def refuse(*a, **k):
        raise AssertionError("should not have asked")

    monkeypatch.setattr(signin.kodi, "select", refuse)
    assert signin.choose_method("TorBox", ("key",)) == signin.KEY


def test_only_the_methods_a_service_has_are_offered(monkeypatch):
    shown = {}
    monkeypatch.setattr(signin.kodi, "select",
                        lambda labels, heading="", **kw:
                            shown.setdefault("labels", labels) and 0 or 0)
    signin.choose_method("Real-Debrid", ("scan", "link"))
    assert len(shown["labels"]) == 2
    assert signin.kodi.localize(32462) not in shown["labels"], \
        "a service with no typed key must not offer one"


def test_backing_out_of_the_chooser_chooses_nothing(monkeypatch):
    monkeypatch.setattr(signin.kodi, "select", lambda *a, **k: -1)
    assert signin.choose_method("Fake", ("scan", "key")) is None


def test_a_service_with_no_methods_at_all_is_not_offered(monkeypatch):
    assert signin.choose_method("Fake", ()) is None


# --------------------------------------------------------------------------
# what a poll answer means
# --------------------------------------------------------------------------


def _capture_window(monkeypatch):
    """Replace the window with something that just drives the poll."""
    seen = {}

    def open_auth(title, url, code="", message="", poll=None, interval=5,
                  scan=True):
        seen.update(title=title, url=url, code=code, scan=scan,
                    message=message)
        results = []
        if poll:
            for _ in range(20):
                answer = poll()
                results.append(answer)
                if answer is None:
                    break
        seen["polled"] = results

    import katan.ui.auth_window as auth
    monkeypatch.setattr(auth, "open_auth", open_auth)
    return seen


def test_a_successful_poll_is_a_successful_sign_in(monkeypatch):
    seen = _capture_window(monkeypatch)
    calls = []

    def poll():
        calls.append(1)
        return len(calls) >= 3

    assert signin.run_device("Fake", "https://example.test", "AB12", poll,
                             lifetime=600, interval=0) is True
    assert len(calls) == 3, "it should stop as soon as it succeeds"
    assert seen["code"] == "AB12"


def test_a_dead_code_stops_rather_than_waiting_it_out(monkeypatch):
    """None means "this will never work". Treating it as "not yet" would
    leave the screen up for the full ten minutes."""
    _capture_window(monkeypatch)
    calls = []

    def poll():
        calls.append(1)
        return None

    assert signin.run_device("Fake", "https://example.test", "AB12", poll,
                             lifetime=600, interval=0) is False
    assert len(calls) == 1


def test_a_poll_that_raises_does_not_take_the_screen_with_it(monkeypatch):
    _capture_window(monkeypatch)

    def poll():
        raise ValueError("the network went away")

    assert signin.run_device("Fake", "https://example.test", "AB12", poll,
                             lifetime=600, interval=0) is False


def test_running_out_of_time_is_not_a_sign_in(monkeypatch):
    _capture_window(monkeypatch)
    assert signin.run_device("Fake", "https://example.test", "AB12",
                             lambda: False, lifetime=0, interval=0) is False


def test_asking_for_a_link_does_not_draw_a_code(monkeypatch):
    """"Open a link" and "scan a code" are different requests."""
    seen = _capture_window(monkeypatch)
    signin.run_device("Fake", "https://example.test", "AB12", lambda: True,
                      lifetime=600, interval=0, scan=False)
    assert seen["scan"] is False


# --------------------------------------------------------------------------
# the typed key
# --------------------------------------------------------------------------


def test_a_typed_key_is_stripped(monkeypatch):
    monkeypatch.setattr(signin.kodi, "keyboard",
                        lambda default="", heading="": "  abc123  ")
    assert signin.ask_for_key("Fake") == "abc123"


def test_cancelling_the_keyboard_is_not_an_empty_key(monkeypatch):
    """An empty string means "clear it". None means "I changed my mind"."""
    monkeypatch.setattr(signin.kodi, "keyboard",
                        lambda default="", heading="": None)
    assert signin.ask_for_key("Fake") is None


def test_the_key_page_is_offered_before_the_keyboard(monkeypatch):
    """Finding an API key on a phone beats hunting for it on a television."""
    order = []
    monkeypatch.setattr(signin, "show_url",
                        lambda *a, **k: order.append("shown"))
    monkeypatch.setattr(signin.kodi, "keyboard",
                        lambda default="", heading="": order.append("typed") or "k")
    signin.ask_for_key("Fake", "", help_url="https://example.test/key")
    assert order == ["shown", "typed"]


def test_no_help_url_means_no_extra_screen(monkeypatch):
    monkeypatch.setattr(signin, "show_url",
                        lambda *a, **k: pytest.fail("nothing to show"))
    monkeypatch.setattr(signin.kodi, "keyboard",
                        lambda default="", heading="": "k")
    assert signin.ask_for_key("Fake", "") == "k"


# --------------------------------------------------------------------------
# the clients
# --------------------------------------------------------------------------


def test_every_service_declares_only_methods_it_handles():
    """A method offered is a method `authorize` has to do something with."""
    from katan.debrid import registry

    for name in registry.names():
        client = registry.get(name)
        if client is None:
            continue
        assert client.methods, "%s offers no way to sign in" % name
        for method in client.methods:
            assert method in (signin.SCAN, signin.LINK, signin.KEY), \
                "%s offers an unknown method %r" % (name, method)
        if signin.KEY in client.methods:
            assert client.key_url, \
                "%s offers a typed key with nowhere to find it" % name


def test_a_service_with_a_typed_key_keeps_the_old_one_if_the_new_one_fails(
        monkeypatch, settings_module):
    """Mistyping a replacement key must not sign you out of a working
    account, which is what setting it before checking it did."""
    from katan.debrid import torbox

    settings_module.set("torbox.apikey", "the-good-key")
    client = torbox.TorBox()
    monkeypatch.setattr(client, "account_info", lambda: None)
    monkeypatch.setattr(signin, "ask_for_key",
                        lambda *a, **k: "a-typo")

    assert client.authorize("key") is False
    assert settings_module.get("torbox.apikey") == "the-good-key"


def test_signing_out_forgets_every_credential(settings_module):
    from katan.debrid import torbox

    settings_module.set("torbox.apikey", "a-key")
    client = torbox.TorBox()
    assert client.configured()
    assert client.sign_out() is True
    assert settings_module.get("torbox.apikey") == ""
    assert client.sign_out() is False, "nothing left to sign out of"


def test_every_service_can_say_what_its_credentials_are():
    """Sign-out clears named settings rather than guessing at them."""
    from katan.debrid import registry

    for name in registry.names():
        client = registry.get(name)
        if client is None:
            continue
        keys = client.credential_settings()
        assert keys, "%s cannot say what to clear to sign out" % name
        for key in keys:
            assert key.startswith(name.replace("-", "")) or "." in key
