"""Kan, the Israeli public broadcaster.

Kan programme pages list their episodes as links back into the same content
tree, and a great deal of Kan output is also published on their YouTube
channel. Both routes are supported: an episode page that yields a manifest is
played directly, and one that only embeds YouTube is handed to the YouTube
add-on, which is how the Kodi POV IL build plays Kan items too.
"""
import re

from ... import kodi, router
from ...meta import items
from . import page

BASE = "https://www.kan.org.il"

# **Kan Educational is a second site and a second content tree.** The
# children's channel lives at kankids.org.il under `/content/kids/`, with the
# same CMS, the same `p-<id>` programme pages and the same season-in-the-
# address convention - and this module accepted only `/content/kan/`, so
# every episode link on it was thrown away. Measured against the bundled
# catalogue: 102 entries under "כאן חינוכית 23" listed no episodes at all,
# which is the whole of the kids channel, and is the half of Kan that the
# household this is built for actually watches.
_CONTENT_TREES = ("/content/kan/", "/content/kids/")


def _in_a_content_tree(href):
    return any(tree in (href or "") for tree in _CONTENT_TREES)


# Episode links live under the same programme path and end in a numeric id.
_EPISODE_LINK = re.compile(
    r'<a[^>]+href="((?:https?://[^"]*kan(?:kids)?\.org\.il)?'
    r'/content/(?:kan|kids)/[^"]*?/\d+/?)"[^>]*>',
    re.I)
_TITLE_ATTR = re.compile(r'title="([^"]{2,120})"')
_IMAGE = re.compile(r'(?:data-src|src)="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"')


def episodes(ref, mode=""):
    """List the episodes of a Kan programme."""
    url = page.absolute(ref, BASE)
    html = page.fetch(url)
    if not html:
        return []

    # **One episode is often two anchors**, and the first one found was the
    # wrong one. A podcast card is an image anchor - `class="card-img
    # card-media"`, an `<img>` and a play icon, no text in it at all - with
    # the name in a second anchor beside it pointing at the same episode.
    # Taking the first and skipping the rest as already seen titled every
    # Kan podcast episode after the number in its own URL: "18408", "852395".
    # So the anchors are gathered per episode and the best of them is used.
    order = []
    anchors = {}
    for match in re.finditer(
            r'<a[^>]+href="([^"]+/\d+/?)"[^>]*>(.*?)</a>', html, re.S | re.I):
        href, inner = match.group(1), match.group(2)
        if not _in_a_content_tree(href):
            continue
        # Relative hrefs on a kankids page belong to kankids, not to
        # kan.org.il - `absolute` is given the page's own host rather than
        # the module's, or every kids episode resolves to a 404 on the wrong
        # site.
        link = page.absolute(href, url)
        # An episode lives under its programme. Without this the station's
        # "frequencies and channels" article is listed as the first episode of
        # every programme on the site.
        if not page.is_descendant(link, url):
            continue
        if link not in anchors:
            anchors[link] = []
            order.append(link)
        anchors[link].append(inner)

    found = [_episode_item(link, anchors[link]) for link in order]

    if not found:
        # Some programmes are a single video rather than a series.
        stream_url, adaptive, strategy = page.extract_stream(html, url)
        if stream_url:
            kodi.log("kan: single video page, matched via %s" % strategy)
            found.append(_direct_item(url, html))

    return found


# Kan puts the season in the address and nowhere else: an episode of "מהצד
# השני" lives at ".../p-12463/s4/1094610/". Without reading it, a programme
# that has run for four seasons is one list of six hundred and twelve.
_SEASON_IN_URL = re.compile(r"/s(\d{1,2})/")


def _season_of(link):
    match = _SEASON_IN_URL.search(link or "")
    return int(match.group(1)) if match else 0


# **An episode card is a heading and a paragraph, and flattening the whole
# anchor ran them together.** Kan writes
#
#     <h3 class="cr-title">פרק 2 - הג'ל של ראמזי</h3>
#     <p class="cr-subtitle">יום העצמאות בפתח ואביחי מועמד לתואר יקיר העיר</p>
#
# and `plain_text` over the anchor returned both as one string, so every
# episode of קופה ראשית was titled "פרק 2 - הג'ל של ראמזי יום העצמאות בפתח
# ואביחי מועמד..." - truncated at 120 characters, with the name and the
# synopsis indistinguishable - while `plot` stayed empty. Which is what "it
# did not find the episode names" looks like from the sofa.
_CARD_TITLE = re.compile(
    r"<h[1-6][^>]*class=\"[^\"]*title[^\"]*\"[^>]*>(.*?)</h[1-6]>", re.S | re.I)
_CARD_PLOT = re.compile(
    r"<p[^>]*class=\"[^\"]*subtitle[^\"]*\"[^>]*>(.*?)</p>", re.S | re.I)


def _episode_item(link, inner_html):
    """One episode, built from every anchor that points at it.

    `inner_html` is a list, because a card is often an image anchor and a
    text anchor side by side - see `episodes`. Whichever of them carries the
    heading wins, whichever carries the picture supplies it, and the number
    in the URL is the last resort rather than the first answer.
    """
    pieces = ([inner_html] if isinstance(inner_html, str)
              else list(inner_html or []))
    joined = "\n".join(pieces)
    heading = _CARD_TITLE.search(joined)
    subtitle = _CARD_PLOT.search(joined)
    # The flattened anchor stays as the fallback: a card with no heading is
    # still an episode, and that is what every other Kan layout gave us
    # before this one was looked at. The longest of them, because the image
    # anchor flattens to nothing and would otherwise win by being first.
    flattened = max((page.plain_text(piece) for piece in pieces),
                    key=len, default="")
    title = (page.plain_text(heading.group(1)) if heading
             else flattened
             or link.rstrip("/").rsplit("/", 1)[-1])
    image = _IMAGE.search(joined)
    return items.new_item(
        "vod",
        ids={"vod": link},
        title=title,
        plot=page.plain_text(subtitle.group(1), limit=600) if subtitle else "",
        season=_season_of(link),
        # Against the episode's own link, not BASE: a kankids poster is
        # relative to kankids.
        art={"poster": page.absolute(image.group(1), link) if image else "",
             "thumb": page.absolute(image.group(1), link) if image else ""},
        extra={"url": router.url_for("play_vod", module="kan", ref=link),
               "module": "kan", "ref": link},
    )


def _direct_item(url, html):
    title = ""
    match = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if match:
        title = page.plain_text(match.group(1))
    return items.new_item(
        "vod",
        ids={"vod": url},
        title=title or "Kan",
        extra={"url": router.url_for("play_vod", module="kan", ref=url),
               "module": "kan", "ref": url},
    )


def stream(ref, mode=""):
    """Resolve one Kan episode to something playable."""
    url = page.absolute(ref, BASE)

    # A programme URL can carry the YouTube id directly in its path, which
    # saves a page fetch entirely.
    video = page.youtube_id(url)
    if video:
        return page.YOUTUBE_PLUGIN % video, False

    html = page.fetch(url)
    if not html:
        return "", False

    found, adaptive, strategy = page.extract_stream(html, url)
    if found:
        kodi.log("kan: resolved via %s" % strategy)
    else:
        kodi.log("kan: no strategy matched for %s" % page._host(url))
    return found, adaptive
