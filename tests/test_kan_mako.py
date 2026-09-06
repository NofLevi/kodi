"""Kan and Mako: the two original extractors, and the bug they both had.

Both listed their site's own navigation as episodes. Mako's menu contributed
five entries to every programme - home, LIVE, programmes, playlists, subscribe -
and Kan's contributed a "frequencies and channels" article, and because they
appear first in the markup they sorted to the top. The first episode of every
programme on both broadcasters was a menu item that plays nothing.

The rule that fixes it is that an episode lives under its programme, and the
tests below are mostly about the ways that comparison can go wrong.
"""
import pytest

from katan import http
from katan.vod.extractors import kan, mako, page


class Response(object):
    def __init__(self, text, status=200):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status


# --------------------------------------------------------------------------
# the rule itself
# --------------------------------------------------------------------------


def test_an_episode_under_the_programme_is_a_descendant():
    assert page.is_descendant(
        "https://www.kan.org.il/content/kan/kan-11/p-12317/s3/8339/",
        "https://www.kan.org.il/content/kan/kan-11/p-12317/")


def test_an_unrelated_article_is_not():
    assert not page.is_descendant(
        "https://www.kan.org.il/content/kan/radio_articles/364664/",
        "https://www.kan.org.il/content/kan/kan-11/p-12317/")


def test_the_programme_itself_is_not_its_own_episode():
    url = "https://www.kan.org.il/content/kan/kan-11/p-12317/"
    assert not page.is_descendant(url, url)
    assert not page.is_descendant(url.rstrip("/"), url)


def test_a_link_up_the_tree_is_not_a_descendant():
    """Mako's home link is an ancestor of every programme on the site."""
    assert not page.is_descendant(
        "https://www.mako.co.il/mako-vod",
        "https://www.mako.co.il/mako-vod-music24/happy_friday")


def test_www_does_not_break_the_comparison():
    """The bundled catalogue says kan.org.il; the markup says www.kan.org.il."""
    assert page.is_descendant(
        "https://www.kan.org.il/content/kan/kan-11/p-12317/s3/8339/",
        "https://kan.org.il/content/kan/kan-11/p-12317/")


def test_a_query_string_does_not_break_it():
    assert page.is_descendant(
        "https://www.mako.co.il/a/b/ep.htm?x=1",
        "https://www.mako.co.il/a/b")


def test_a_run_on_name_is_not_a_descendant():
    assert not page.is_descendant("https://x.com/showtwo/ep",
                                  "https://x.com/show")


def test_a_dash_still_counts_as_a_boundary():
    """Mako separates an episode from its programme with a dash, not a slash."""
    assert page.is_descendant(
        "https://www.mako.co.il/mako-vod-music24/happy_friday-s1/VOD-a.htm",
        "https://www.mako.co.il/mako-vod-music24/happy_friday")


def test_nonsense_is_not_a_descendant():
    assert not page.is_descendant("", "https://x.com/a")
    assert not page.is_descendant("https://x.com/a", "")


# --------------------------------------------------------------------------
# what that does to the listings
# --------------------------------------------------------------------------

KAN_PROGRAMME = "https://kan.org.il/content/kan/kan-11/p-12317/"
KAN_PAGE = """
<a href="/content/kan/radio_articles/364664/">frequencies and channels</a>
<a href="/content/kan/kan-11/p-12317/s3/8339/">episode 1</a>
<a href="/content/kan/kan-11/p-12317/s3/8361/">episode 2</a>
<a href="/content/kan/kan-11/p-12317/">the programme itself</a>
"""

MAKO_PROGRAMME = "https://www.mako.co.il/mako-vod-music24/happy_friday"
MAKO_PAGE = """
<a href="https://www.mako.co.il/mako-vod">home</a>
<a href="https://www.mako.co.il/mako-vod-live-tv/VOD-1.htm">LIVE</a>
<a href="https://www.mako.co.il/mako-vod-index">programmes</a>
<a href="https://www.mako.co.il/mako-vod-purchase">subscribe</a>
<a href="https://www.mako.co.il/mako-vod-music24/happy_friday-s1/VOD-a.htm">ep one</a>
<a href="https://www.mako.co.il/mako-vod-music24/happy_friday-s1/VOD-b.htm">ep two</a>
"""


@pytest.fixture
def kan_site(monkeypatch):
    monkeypatch.setattr(http, "get", lambda url, **kw: Response(KAN_PAGE))


@pytest.fixture
def mako_site(monkeypatch):
    monkeypatch.setattr(http, "get", lambda url, **kw: Response(MAKO_PAGE))


def test_kan_does_not_list_the_station_article(kan_site):
    found = kan.episodes(KAN_PROGRAMME)
    refs = [item["extra"]["ref"] for item in found]
    assert refs, "the real episodes should still be listed"
    assert not any("radio_articles" in ref for ref in refs)


def test_kan_lists_the_real_episodes(kan_site):
    found = kan.episodes(KAN_PROGRAMME)
    assert len(found) == 2
    assert all("/p-12317/s3/" in item["extra"]["ref"] for item in found)


def test_mako_does_not_list_its_own_menu(mako_site):
    found = mako.episodes(MAKO_PROGRAMME)
    refs = [item["extra"]["ref"] for item in found]
    assert refs
    for junk in ("/mako-vod-index", "/mako-vod-purchase", "/mako-vod-live-tv"):
        assert not any(junk in ref for ref in refs), junk
    assert not any(ref.rstrip("/").endswith("/mako-vod") for ref in refs)


def test_mako_lists_the_real_episodes(mako_site):
    found = mako.episodes(MAKO_PROGRAMME)
    assert len(found) == 2
    assert all("happy_friday-s1/" in item["extra"]["ref"] for item in found)


# --------------------------------------------------------------------------
# mako VOD is signed too
# --------------------------------------------------------------------------


def test_a_mako_vod_stream_is_signed(monkeypatch):
    """The on-demand CDN refuses an unsigned request exactly as the live one does."""
    manifest = "https://d3par5938i20jq.cloudfront.net/hls/VOD/a/index.m3u8"
    calls = []

    def fake_get(url, **kwargs):
        return Response('<script>{"contentUrl": "%s"}</script>'
                        '<script type="application/ld+json">'
                        '{"@type":"VideoObject","contentUrl":"%s"}</script>'
                        % (manifest, manifest))

    def fake_get_json(url, default=None, **kwargs):
        calls.append(kwargs.get("params") or {})
        return {"status": "Success",
                "tickets": [{"vendor": "AWS", "ticket": "token=jwt-here"}]}

    monkeypatch.setattr(http, "get", fake_get)
    monkeypatch.setattr(http, "get_json", fake_get_json)

    url, _adaptive = mako.stream("https://www.mako.co.il/a/VOD-1.htm")
    assert "token=jwt-here" in url
    assert calls and calls[0]["rv"] == "AWS", \
        "the on-demand CDN wants the AWS ticket, not the Akamai one"


def test_a_youtube_handoff_is_not_signed(monkeypatch):
    """A plugin:// path is not a CDN URL and must be left alone."""
    monkeypatch.setattr(
        http, "get",
        lambda url, **kw: Response(
            '<iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>'))
    monkeypatch.setattr(http, "get_json", lambda url, default=None, **kw:
                        pytest.fail("a YouTube handoff must not ask for a ticket"))
    url, _adaptive = mako.stream("https://www.mako.co.il/a/VOD-1.htm")
    assert url.startswith("plugin://")
