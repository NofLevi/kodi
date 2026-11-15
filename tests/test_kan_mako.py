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

from pinky import http
from pinky.vod.extractors import kan, mako, page


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


# --------------------------------------------------------------------------
# Kan Educational, the podcasts, and the radio - the three parts of Kan this
# module could not reach, each found by running tools/check_vod.py
# --------------------------------------------------------------------------

KIDS_PROGRAMME = "https://www.kankids.org.il/content/kids/hinuchit-main/on_our_own/"
KIDS_PAGE = u"""
<a href="/content/kids/hinuchit-main/on_our_own/s5/960776/">
  <div class="cr-card image-block"><img src="/media/a/one.jpg?width=490&amp;height=272" /></div>
  <div class="cr-text">
    <h3 class="cr-title max-lines-3">פרק 1 - הג&#x27;ל</h3>
    <p class="cr-subtitle max-lines-3">תקציר הפרק</p>
  </div>
</a>
<a href="/content/kids/hinuchit-main/on_our_own/s5/961523/">
  <div class="cr-text"><h3 class="cr-title">פרק 2</h3></div>
</a>
"""


@pytest.fixture
def kids_site(monkeypatch):
    monkeypatch.setattr(http, "get", lambda url, **kw: Response(KIDS_PAGE))


def test_the_kan_kids_content_tree_is_reached(kids_site):
    """Kan Educational is a second site - kankids.org.il, /content/kids/ -
    and this module accepted only /content/kan/, so all 102 catalogue
    entries under "כאן חינוכית 23" listed nothing at all."""
    found = kan.episodes(KIDS_PROGRAMME)
    assert len(found) == 2
    assert all("/content/kids/" in item["extra"]["ref"] for item in found)


def test_a_kids_episode_link_stays_on_the_kids_host(kids_site):
    """A relative href on a kankids page belongs to kankids. Resolved against
    the module's own BASE it becomes a 404 on kan.org.il."""
    found = kan.episodes(KIDS_PROGRAMME)
    assert all(item["extra"]["ref"].startswith("https://www.kankids.org.il")
               for item in found)
    assert found[0]["art"]["poster"].startswith("https://www.kankids.org.il")


def test_the_card_heading_is_the_title_and_the_paragraph_is_the_plot(kids_site):
    """Flattening the whole anchor ran them together, so every episode was
    named "פרק 1 - הג'ל תקציר הפרק" with the synopsis inside the title and
    `plot` left empty."""
    first = kan.episodes(KIDS_PROGRAMME)[0]
    # The apostrophe is written `&#x27;` in the markup, so this also pins the
    # unescaping: `plain_text` stripped tags and never decoded entities, and
    # every broadcaster in this folder goes through it.
    assert first["title"] == u"פרק 1 - הג'ל"
    assert first["plot"] == u"תקציר הפרק"


def test_the_poster_query_string_is_not_left_escaped(kids_site):
    """`&amp;` in an img src is one parameter named `width` with `&amp;height`
    glued on, and the image does not load."""
    assert "&amp;" not in kan.episodes(KIDS_PROGRAMME)[0]["art"]["poster"]


PODCAST_PROGRAMME = "https://www.kan.org.il/content/kan/podcasts/p-8582/"
PODCAST_PAGE = u"""
<a href="/content/kan/podcasts/p-8582/18408/" class="card-img card-media">
  <img src="/media/x/cover.jpg" /><span class="icon icon-play"></span>
</a>
<a href="/content/kan/podcasts/p-8582/18408/" class="card-text">
  <h3 class="cr-title">החיים כניגון</h3>
</a>
"""


def test_an_episode_split_across_two_anchors_keeps_its_name(monkeypatch):
    """A podcast card is an image anchor with no text beside a text anchor
    pointing at the same episode. Taking the first and skipping the rest as
    already seen named every Kan podcast after the number in its own URL."""
    monkeypatch.setattr(http, "get", lambda url, **kw: Response(PODCAST_PAGE))
    found = kan.episodes(PODCAST_PROGRAMME)
    assert len(found) == 1
    assert found[0]["title"] == u"החיים כניגון"


RADIO_PROGRAMME = "https://www.kan.org.il/content/kan/kan-b/p-10333/"
RADIO_PAGE = u"""
<div class="radio-item">
  <h3 class="radio-title">14.11.2026-יומן</h3>
  <button data-isHls="False" data-player-id="102388"
          data-player-title="14.11.2026-יומן"
          data-player-src="https://traffic.omny.fm/d/clips/a/one/audio.mp3?DIST=KAN"></button>
</div>
<div class="radio-item">
  <button data-player-title="7.11.2026-יומן"
          data-player-src="https://traffic.omny.fm/d/clips/a/two/audio.mp3?DIST=KAN"></button>
</div>
"""


@pytest.fixture
def radio_site(monkeypatch):
    monkeypatch.setattr(http, "get", lambda url, **kw: Response(RADIO_PAGE))


def test_a_radio_programme_lists_its_broadcasts(radio_site):
    """Kan's radio and podcast pages - some 470 catalogue entries - list a
    `radio-item` per broadcast with the audio in a data attribute and no page
    of its own. With nothing reading those, every one of them fell through to
    the single-item fallback and offered the latest broadcast as the whole
    programme."""
    found = kan.episodes(RADIO_PROGRAMME)
    assert len(found) == 2
    assert found[0]["title"] == u"14.11.2026-יומן"
    assert found[0]["extra"]["ref"].endswith("one/audio.mp3?DIST=KAN")


def test_the_date_kan_puts_in_the_title_becomes_the_date(radio_site):
    found = kan.episodes(RADIO_PROGRAMME)
    assert [item["premiered"] for item in found] == ["2026-11-14", "2026-11-07"]


def test_a_radio_reference_is_played_without_fetching_a_page(radio_site, monkeypatch):
    """The reference *is* the audio file, so there is no document to scrape -
    and fetching one would download the programme instead."""
    def refuse(*args, **kwargs):
        raise AssertionError("kan.stream fetched a page for an audio file")
    monkeypatch.setattr(page, "fetch", refuse)
    url, adaptive = kan.stream(
        "https://traffic.omny.fm/d/clips/a/one/audio.mp3?DIST=KAN")
    assert url.endswith("one/audio.mp3?DIST=KAN")
    assert adaptive is False


def test_an_audio_page_resolves_through_the_ladder():
    """Not everything a broadcaster publishes is video. The ladder had rungs
    for ld+json, HLS, Kaltura and YouTube only, so a page carrying a plain
    MP3 listed its episodes and played nothing."""
    url, adaptive, strategy = page.extract_stream(
        u'<audio src="https://traffic.omny.fm/d/clips/a/b/audio.mp3?DIST=KAN&amp;x=1">',
        "https://www.kan.org.il/content/kan/podcasts/p-1/2/")
    assert strategy == "audio file"
    assert url == "https://traffic.omny.fm/d/clips/a/b/audio.mp3?DIST=KAN&x=1"
    assert adaptive is False


def test_a_manifest_still_wins_over_an_audio_file():
    """A page with both is a video page with a podcast version of itself
    somewhere on it, and the manifest is the better answer."""
    _url, _adaptive, strategy = page.extract_stream(
        u'<audio src="https://x/a.mp3"><video src="https://y/b.m3u8">', "")
    assert strategy == "m3u8"


DIG_PROGRAMME = "https://kan.org.il/content/dig/digital/p-12933/"
DIG_PAGE = u"""
<a href="https://www.kan.org.il/content/kan/radio_articles/364664/">frequencies</a>
<a href="https://www.kan.org.il/content/dig/digital/p-12933/105600/">
  <div class="cr-text"><h3 class="cr-title">פרק א</h3></div>
</a>
<a href="https://www.kan.org.il/content/dig/digital/p-12933/106563/">
  <div class="cr-text"><h3 class="cr-title">פרק ב</h3></div>
</a>
"""


def test_a_third_kan_content_tree_needs_no_new_pattern(monkeypatch):
    """Naming the trees was the mistake. `/content/kan/` alone hid the whole
    kids channel; adding `/content/kids/` then turned up `/content/dig/`,
    where the digital-only programmes live, and there is no reason to think
    that is the last one. What keeps the station's own article out is
    `is_descendant`, which never cared which tree it was in."""
    monkeypatch.setattr(http, "get", lambda url, **kw: Response(DIG_PAGE))
    found = kan.episodes(DIG_PROGRAMME)
    assert len(found) == 2
    assert [item["title"] for item in found] == [u"פרק א",
                                                 u"פרק ב"]


def test_the_station_article_is_still_excluded_from_any_tree(monkeypatch):
    """The guard that replaced the tree names has to still do their job."""
    monkeypatch.setattr(http, "get", lambda url, **kw: Response(DIG_PAGE))
    refs = [item["extra"]["ref"] for item in kan.episodes(DIG_PROGRAMME)]
    assert not any("radio_articles" in ref for ref in refs)
