"""Broadcaster tickets: the thing that makes Keshet 12 play at all.

The whole point of entitlement.py is that it stays cheap. A ticket is granted
for the whole broadcaster and only the first request needs one, so the tests
that matter here are the ones that stop it quietly becoming a request per
channel or a refresh loop behind every stream.
"""
import json

import pytest

from katan import cache, http
from katan.vod import channels, entitlement

# Shaped like a real Akamai ticket, but the hmac is filler. A ticket is a
# fifteen minute grant on a free-to-air stream, so a real one would be worth
# nothing by the time anyone read it, but a fixture that cannot be mistaken for
# a live credential is the better habit.
TICKET = ("hdnea=st%3D1700000000%7Eexp%3D1700000900%7Eacl%3D%2F*"
          "%7Ehmac%3Dnot-a-real-hmac-this-is-a-test-fixture")


@pytest.fixture
def service(monkeypatch):
    """A stand-in Mako entitlement service that records what it was asked."""
    calls = []

    def fake_get_json(url, default=None, **kwargs):
        calls.append({"url": url, "params": kwargs.get("params") or {},
                      "headers": kwargs.get("headers") or {}})
        return {"caseId": "1", "status": "Success",
                "tickets": [{"vendor": "AKAMAI", "ticket": TICKET,
                             "url": (kwargs.get("params") or {}).get("lp", "")}]}

    monkeypatch.setattr(http, "get_json", fake_get_json)
    return calls


# --------------------------------------------------------------------------
# signing
# --------------------------------------------------------------------------


def test_a_ticket_is_appended_to_the_stream_url(service):
    url = entitlement.sign(
        "https://mako-streaming.akamaized.net/stream/hls/live/2033791/k12/index.m3u8",
        "mako")
    assert url.endswith("?" + TICKET)
    assert len(service) == 1


def test_a_link_that_already_has_a_query_keeps_it(service):
    url = entitlement.sign(
        "https://mako-streaming.akamaized.net/direct/hls/live/2035340/"
        "ch24live/index.m3u8?as=1", "mako")
    assert "?as=1&" + TICKET in url, "the ticket must not clobber the query"


def test_the_service_is_asked_for_the_path_not_the_whole_url(service):
    entitlement.sign(
        "https://mako-streaming.akamaized.net/stream/hls/live/2033791/k12/"
        "index.m3u8?as=1", "mako")
    assert service[0]["params"]["lp"] == \
        "/stream/hls/live/2033791/k12/index.m3u8?as=1"
    assert service[0]["params"]["rv"] == "AKAMAI"
    assert service[0]["params"]["et"] == "gt"


def test_the_request_looks_like_a_browser(service):
    """Mako answers a default user agent with a 403."""
    entitlement.sign("https://mako-streaming.akamaized.net/a/index.m3u8", "mako")
    headers = service[0]["headers"]
    assert "Mozilla" in headers.get("User-Agent", "")
    assert "mako.co.il" in headers.get("Referer", "")


# --------------------------------------------------------------------------
# staying cheap
# --------------------------------------------------------------------------


def test_one_ticket_covers_every_channel(service):
    """The grant is acl=/*, so minting per channel would be pure waste."""
    for path in ("/stream/hls/live/2033791/k12/index.m3u8",
                 "/evrideo/hls/live/20001278/free_food/index.m3u8",
                 "/direct/hls/live/2035340/ch24live/index.m3u8?as=1",
                 "/evrideo/hls/live/20001278/savri/index.m3u8"):
        signed = entitlement.sign("https://mako-streaming.akamaized.net" + path,
                                  "mako")
        assert TICKET in signed

    assert len(service) == 1, \
        "four channels must not cost four requests to the entitlement service"


def test_a_cached_ticket_is_reused(service):
    assert entitlement.mako_ticket("/a") == TICKET
    assert entitlement.mako_ticket("/a") == TICKET
    assert len(service) == 1


def test_a_refresh_mints_a_new_one(service):
    entitlement.mako_ticket("/a")
    entitlement.mako_ticket("/a", refresh=True)
    assert len(service) == 2


def test_listing_the_channels_never_asks_for_a_ticket(no_network):
    """Signing belongs to playback. Browsing must not touch the service."""
    listed = channels.live_channels(kind="tv")
    assert listed, "expected the bundled list to have television channels"


# --------------------------------------------------------------------------
# failing honestly
# --------------------------------------------------------------------------


def test_a_refusal_leaves_the_url_unsigned(monkeypatch):
    """A 403 the user can see beats a channel that silently disappears."""
    monkeypatch.setattr(http, "get_json",
                        lambda url, default=None, **kw: {"status": "Failure"})
    plain = "https://mako-streaming.akamaized.net/a/index.m3u8"
    assert entitlement.sign(plain, "mako") == plain


def test_a_service_that_does_not_answer_leaves_the_url_unsigned(monkeypatch):
    monkeypatch.setattr(http, "get_json", lambda url, default=None, **kw: None)
    plain = "https://mako-streaming.akamaized.net/a/index.m3u8"
    assert entitlement.sign(plain, "mako") == plain


def test_an_empty_ticket_list_is_not_appended(monkeypatch):
    monkeypatch.setattr(http, "get_json",
                        lambda url, default=None, **kw: {"status": "Success",
                                                         "tickets": []})
    plain = "https://mako-streaming.akamaized.net/a/index.m3u8"
    assert entitlement.sign(plain, "mako") == plain


def test_a_failed_ticket_is_not_cached(monkeypatch):
    calls = []

    def flaky(url, default=None, **kwargs):
        calls.append(url)
        return None

    monkeypatch.setattr(http, "get_json", flaky)
    entitlement.mako_ticket("/a")
    entitlement.mako_ticket("/a")
    assert len(calls) == 2, "a failure must not poison the cache for ten minutes"


def test_an_unknown_provider_leaves_the_url_alone(no_network):
    plain = "https://example.com/a.m3u8"
    assert entitlement.sign(plain, "nobody") == plain


def test_no_provider_means_no_request(no_network):
    plain = "https://example.com/a.m3u8"
    assert entitlement.sign(plain, "") == plain


# --------------------------------------------------------------------------
# the channel data and the resolver together
# --------------------------------------------------------------------------


def test_keshet_channels_ask_for_a_ticket(settings_module):
    settings_module.set("vod.channels_url", "")
    channels.refresh()
    keshet = [(key, value) for key, value in channels.load().items()
              if value.get("module") == "keshet"]
    assert keshet, "expected Keshet channels in the bundled list"
    for key, value in keshet:
        assert (value.get("linkDetails") or {}).get("token") == "mako", \
            "%s needs a ticket or it will 403" % key


def test_resolving_a_keshet_channel_signs_it(service, settings_module):
    settings_module.set("vod.channels_url", "")
    channels.refresh()
    url, _headers, _adaptive = channels.resolve("ch_12")
    assert url.startswith("https://mako-streaming.akamaized.net")
    assert TICKET in url, "Keshet 12 without a ticket is a 403"


def test_a_channel_with_no_token_is_untouched(no_network, settings_module):
    settings_module.set("vod.channels_url", "")
    channels.refresh()
    url, _headers, _adaptive = channels.resolve("ch_11")
    assert url and "hdnea" not in url
