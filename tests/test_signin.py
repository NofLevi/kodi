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
    methods = ("scan", "key")
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
    signin.choose_method("Fake", ("key", "scan"))
    labels = shown["labels"]
    # Paste sits between scan and typing: it needs a phone like scan does, but
    # unlike scan it still involves a copy, and it beats typing outright.
    assert labels == [signin.kodi.localize(32460), signin.kodi.localize(32513),
                      signin.kodi.localize(32462)]


def test_a_service_with_one_way_in_is_not_asked(monkeypatch):
    """A one-item menu is a speed bump.

    This used to use TorBox, which had "a key and nothing else" - it now also
    has "paste it from your phone", because anything that can be typed can be
    pasted, so it has two. A device flow with no key is the genuine one-way
    case left.
    """
    def refuse(*a, **k):
        raise AssertionError("should not have asked")

    monkeypatch.setattr(signin.kodi, "select", refuse)
    assert signin.choose_method("Somewhere", ("scan",)) == signin.SCAN


def test_a_key_can_always_be_pasted_instead_of_typed(monkeypatch):
    """Typing a thirty-two character key on a remote is the worst thing this
    add-on asks of anybody, so every service that takes a key offers the
    phone as well - without each client having to say so."""
    shown = {}
    monkeypatch.setattr(signin.kodi, "select",
                        lambda labels, heading="", **kw:
                            shown.setdefault("labels", labels) and 0 or 0)

    signin.choose_method("TorBox", ("key",))
    assert signin.kodi.localize(32513) in shown["labels"]


def test_a_service_with_no_key_is_not_offered_the_phone(monkeypatch):
    """Real-Debrid has no key to paste; offering the page would lead nowhere."""
    shown = {}
    monkeypatch.setattr(signin.kodi, "select",
                        lambda labels, heading="", **kw:
                            shown.setdefault("labels", labels) and 0 or 0)

    signin.choose_method("Real-Debrid", ("scan", "key"))
    assert signin.kodi.localize(32513) in shown["labels"]
    shown.clear()
    signin.choose_method("Real-Debrid", ("scan",))
    assert shown.get("labels") is None, "one way in is not a question"


def test_only_the_methods_a_service_has_are_offered(monkeypatch):
    shown = {}
    monkeypatch.setattr(signin.kodi, "select",
                        lambda labels, heading="", **kw:
                            shown.setdefault("labels", labels) and 0 or 0)

    # Real-Debrid mints its credentials through the device flow, so there is
    # no key anywhere that could be typed or pasted.
    assert signin.choose_method("Real-Debrid", ("scan",)) == signin.SCAN
    assert "labels" not in shown, "one way in is not a question worth asking"

    signin.choose_method("TorBox", ("scan", "key"))
    assert shown["labels"] == [signin.kodi.localize(32460),
                               signin.kodi.localize(32513),
                               signin.kodi.localize(32462)]


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

    def open_auth(title, url, code="", message="", poll=None, interval=5):
        seen.update(title=title, url=url, code=code, message=message)
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


def test_the_link_and_the_code_are_always_on_screen(monkeypatch):
    """There is no separate "open a link" choice, and there need not be.

    It was the same flow with the QR code not drawn, and the screen shows the
    link and the six digits either way - which is exactly what somebody with
    no phone camera needs. Offering it asked the viewer a question about
    themselves that the screen had already answered.
    """
    seen = _capture_window(monkeypatch)
    signin.run_device("Fake", "https://example.test", "AB12", lambda: True,
                      lifetime=600, interval=0)
    assert seen["url"] == "https://example.test"
    assert seen["code"] == "AB12"


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
            assert method in (signin.SCAN, signin.KEY), \
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


# --------------------------------------------------------------------------
# Trakt goes through the same screen as everything else
# --------------------------------------------------------------------------


def _connect_trakt(monkeypatch, choice, signed_in=True):
    """Press the one Trakt button, answering its chooser with `choice`."""
    from katan import settings
    from katan.meta import trakt
    from katan.ui import handlers, wizard

    settings.set_many({"trakt.access_token": "token" if signed_in else "",
                       "trakt.refresh_token": "refresh",
                       "trakt.client_id": "id", "trakt.client_secret": "secret"})
    asked = {}
    monkeypatch.setattr(handlers.kodi, "select",
                        lambda labels, heading="", **kw:
                            asked.setdefault("labels", labels) is None or choice)
    monkeypatch.setattr(trakt, "http", _NoHttp())
    monkeypatch.setattr(wizard, "step_trakt",
                        lambda: asked.__setitem__("signed_in_again", True))
    monkeypatch.setattr(handlers.kodi, "refresh_container", lambda: None)
    monkeypatch.setattr(handlers.kodi, "notify", lambda *a, **k: None)
    handlers.connect({"service": "trakt"})
    return asked


class _NoHttp(object):
    """Trakt's revoke call has nowhere to go in a test."""

    def post(self, *args, **kwargs):
        return None


def test_a_connected_trakt_can_be_signed_out_of(monkeypatch):
    """The whole point of the chooser: an account you can get back out of.

    Trakt had no sign-out anywhere in the interface, so a token that stopped
    working could only be cleared by editing the setting by hand.
    """
    from katan import settings

    asked = _connect_trakt(monkeypatch, choice=1)
    assert "Trakt" in asked["labels"][1]
    assert not settings.get("trakt.access_token")
    assert not settings.get("trakt.refresh_token")
    assert "signed_in_again" not in asked


def test_signing_in_again_leaves_the_account_alone_until_it_succeeds(monkeypatch):
    """Choosing "connect" must not clear what is already working."""
    from katan import settings

    asked = _connect_trakt(monkeypatch, choice=0)
    assert asked["signed_in_again"] is True
    assert settings.get("trakt.access_token") == "token"


def test_cancelling_the_chooser_does_nothing_at_all(monkeypatch):
    from katan import settings

    asked = _connect_trakt(monkeypatch, choice=-1)
    assert "signed_in_again" not in asked
    assert settings.get("trakt.access_token") == "token"


def test_a_disconnected_trakt_goes_straight_to_signing_in(monkeypatch):
    """Nothing to sign out of, so nothing to ask about."""
    asked = _connect_trakt(monkeypatch, choice=1, signed_in=False)
    assert "labels" not in asked
    assert asked["signed_in_again"] is True


def test_a_bundled_application_is_never_asked_for(monkeypatch):
    """The reason "open a link and it connects" was unreachable.

    Trakt needs a registered application before anybody can sign in, and
    without a bundled one the flow opened a keyboard for two long strings
    before it ever showed the link.
    """
    from katan import settings
    from katan.meta import trakt

    settings.set_many({"trakt.client_id": "", "trakt.client_secret": ""})
    assert not trakt.configured()

    monkeypatch.setattr(trakt, "BUNDLED_CLIENT_ID", "bundled-id")
    monkeypatch.setattr(trakt, "BUNDLED_CLIENT_SECRET", "bundled-secret")
    assert trakt.configured()
    assert trakt.client_id() == "bundled-id"
    assert trakt.client_secret() == "bundled-secret"


# --------------------------------------------------------------------------
# every account signs in by opening a link on a phone
#
# Checked against the live services rather than against our own comments,
# because the comment in the TorBox client saying it had no device flow was
# out of date and had been for a while. Probed 2026-09-08:
#
#   Real-Debrid   200, anonymously, with the open-source client id
#   AllDebrid     200, anonymously, agent name only
#   TorBox        200, anonymously, app name only
#   Trakt         401 invalid_client - the flow is there, the application is
#                 not registered yet
#   Premiumize    400 invalid_client - same
# --------------------------------------------------------------------------


def test_every_account_offers_a_phone_sign_in():
    """Not one of them should be asking for a key on a remote."""
    from katan.debrid import registry
    from katan.meta import trakt

    for name in registry.names():
        client = registry.get(name)
        if client is None:
            continue
        assert signin.SCAN in client.methods, \
            "%s has no way in that ends on a phone" % name
    assert signin.SCAN in trakt.methods


def test_a_link_that_carries_the_code_is_preferred(monkeypatch, settings_module):
    """Real-Debrid returns two URLs and only one of them saves the typing.

    `direct_verification_url` has the device id in it, so scanning it
    authorises this box with nothing typed. `verification_url` is the same
    page with the work still to do, and it was the one being used.
    """
    from katan.debrid import realdebrid

    monkeypatch.setattr(realdebrid.http, "get_json", lambda url, **kw: {
        "device_code": "DEVICE", "user_code": "SZUEFDVN", "interval": 5,
        "expires_in": 900,
        "verification_url": "https://real-debrid.com/device",
        "direct_verification_url":
            "https://real-debrid.com/authorize?client_id=X&device_id=DEVICE",
    } if "device/code" in url else None)

    seen = {}
    monkeypatch.setattr(signin, "run_device",
                        lambda title, url, code, poll, **kw:
                        seen.update(url=url, code=code) or False)
    realdebrid.RealDebrid().authorize("scan")

    assert seen["url"].startswith("https://real-debrid.com/authorize?")
    # Still on screen: somebody reading the link off the television needs it.
    assert seen["code"] == "SZUEFDVN"


def test_the_plain_link_is_used_when_there_is_no_direct_one(monkeypatch,
                                                            settings_module):
    from katan.debrid import realdebrid

    monkeypatch.setattr(realdebrid.http, "get_json", lambda url, **kw: {
        "device_code": "DEVICE", "user_code": "AB12",
        "verification_url": "https://real-debrid.com/device",
    } if "device/code" in url else None)
    seen = {}
    monkeypatch.setattr(signin, "run_device",
                        lambda title, url, code, poll, **kw:
                        seen.update(url=url) or False)
    realdebrid.RealDebrid().authorize("scan")

    assert seen["url"] == "https://real-debrid.com/device"


# --------------------------------------------------------------------------
# connecting asks nothing when it does not have to
#
# Every account has a device flow, and it is better than every alternative
# for every viewer: nothing typed, nothing copied, and it finishes on the
# phone already in their hand. Offering a menu was making somebody with a
# remote choose the option we would have chosen for them.
# --------------------------------------------------------------------------


class _Client(object):
    label = "Fake"
    key_url = "https://example.test/key"

    def __init__(self, methods, works=True):
        self.methods = methods
        self.works = works
        self.tried = []

    def authorize(self, method=None):
        self.tried.append(method)
        return self.works

    def authorize_with_key(self, key):
        self.tried.append(("key", key))
        return True


def _no_questions(monkeypatch):
    """Fail loudly if anything puts a list in front of the viewer."""
    from katan.ui import wizard

    def refuse(*args, **kwargs):
        raise AssertionError("the viewer was asked something")

    monkeypatch.setattr(wizard.kodi, "select", refuse)
    return wizard


def test_a_device_flow_just_runs(monkeypatch):
    wizard = _no_questions(monkeypatch)
    client = _Client(("scan", "key"))

    assert wizard.connect(client) is True
    assert client.tried == [signin.SCAN]


def test_the_other_ways_in_appear_only_when_it_fails(monkeypatch):
    """Which is the moment they are worth having."""
    from katan.ui import wizard

    client = _Client(("scan", "key"), works=False)
    offered = {}
    monkeypatch.setattr(wizard.kodi, "select",
                        lambda labels, heading="", **kw:
                        offered.setdefault("labels", labels) is None or -1)

    assert wizard.connect(client) is False
    assert client.tried == [signin.SCAN]
    # Paste and type, and no second offer of the thing that just failed.
    assert offered["labels"] == [signin.kodi.localize(32513),
                                 signin.kodi.localize(32462)]


def test_a_service_with_only_a_device_flow_does_not_show_a_list_of_one(
        monkeypatch):
    """Real-Debrid mints its credentials in the flow; there is no key."""
    wizard = _no_questions(monkeypatch)
    client = _Client(("scan",), works=False)

    assert wizard.connect(client) is False
    assert client.tried == [signin.SCAN]


def test_a_service_with_no_device_flow_still_gets_asked(monkeypatch):
    """Nothing here assumes every client will always have one."""
    from katan.ui import wizard

    client = _Client(("key",))
    monkeypatch.setattr(wizard.kodi, "select",
                        lambda labels, heading="", **kw: len(labels) - 1)
    monkeypatch.setattr(wizard.kodi, "keyboard", lambda *a, **k: "typed-key")
    monkeypatch.setattr(signin, "show_url", lambda *a, **k: None)

    assert wizard.connect(client) is True
    assert client.tried == [signin.KEY]
