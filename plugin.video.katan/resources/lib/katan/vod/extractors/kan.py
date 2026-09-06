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

# Episode links live under the same programme path and end in a numeric id.
_EPISODE_LINK = re.compile(
    r'<a[^>]+href="((?:https?://[^"]*kan\.org\.il)?/content/kan/[^"]*?/\d+/?)"[^>]*>',
    re.I)
_TITLE_ATTR = re.compile(r'title="([^"]{2,120})"')
_IMAGE = re.compile(r'(?:data-src|src)="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"')


def episodes(ref, mode=""):
    """List the episodes of a Kan programme."""
    url = page.absolute(ref, BASE)
    html = page.fetch(url)
    if not html:
        return []

    found = []
    seen = set()
    for match in re.finditer(
            r'<a[^>]+href="([^"]+/\d+/?)"[^>]*>(.*?)</a>', html, re.S | re.I):
        href, inner = match.group(1), match.group(2)
        if "/content/kan/" not in href:
            continue
        link = page.absolute(href, BASE)
        if link in seen:
            continue
        # An episode lives under its programme. Without this the station's
        # "frequencies and channels" article is listed as the first episode of
        # every programme on the site.
        if not page.is_descendant(link, url):
            continue
        seen.add(link)
        found.append(_episode_item(link, inner))

    if not found:
        # Some programmes are a single video rather than a series.
        stream_url, adaptive, strategy = page.extract_stream(html, url)
        if stream_url:
            kodi.log("kan: single video page, matched via %s" % strategy)
            found.append(_direct_item(url, html))

    return found


def _episode_item(link, inner_html):
    title = _text(inner_html) or link.rstrip("/").rsplit("/", 1)[-1]
    image = _IMAGE.search(inner_html)
    return items.new_item(
        "vod",
        ids={"vod": link},
        title=title,
        art={"poster": page.absolute(image.group(1), BASE) if image else "",
             "thumb": page.absolute(image.group(1), BASE) if image else ""},
        extra={"url": router.url_for("play_vod", module="kan", ref=link),
               "module": "kan", "ref": link},
    )


def _direct_item(url, html):
    title = ""
    match = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if match:
        title = _text(match.group(1))
    return items.new_item(
        "vod",
        ids={"vod": url},
        title=title or "Kan",
        extra={"url": router.url_for("play_vod", module="kan", ref=url),
               "module": "kan", "ref": url},
    )


def _text(html):
    """Strip tags and collapse whitespace out of a link body."""
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:120]


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
        kodi.log("kan: no strategy matched for %s" % url)
    return found, adaptive
