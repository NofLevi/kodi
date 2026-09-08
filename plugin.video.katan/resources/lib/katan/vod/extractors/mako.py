"""Mako, which carries Keshet 12 and its sister channels.

Mako pages are heavier than Kan's but follow the same shape: a programme page
listing episode links, and an episode page carrying either a manifest or a
Kaltura entry id. The VOD ids in the catalogue are full mako.co.il URLs, so no
id translation is needed.
"""
import re

from ... import kodi, router
from ...meta import items
from . import page

BASE = "https://www.mako.co.il"

_EPISODE_LINK = re.compile(
    r'<a[^>]+href="((?:https?://(?:www\.)?mako\.co\.il)?/[^"]*?[Vv][Oo][Dd][^"]*?)"',
    re.I)
_IMAGE = re.compile(r'(?:data-src|src)="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"')

# The last path segment of an episode address, and only of an episode address.
_EPISODE_DOCUMENT = re.compile(r"VOD-[^/]+\.htm", re.I)


def episodes(ref, mode=""):
    url = page.absolute(ref, BASE)
    html = page.fetch(url)
    if not html:
        return []

    found = []
    seen = set()
    for match in re.finditer(
            r'<a[^>]+href="([^"]*vod[^"]*)"[^>]*>(.*?)</a>', html, re.S | re.I):
        href, inner = match.group(1), match.group(2)
        link = page.absolute(href, BASE)
        if link in seen:
            continue
        # Mako's own menu - home, LIVE, programmes, playlists, subscribe - all
        # contain "vod" and so match the same pattern an episode does. An
        # episode lives under its programme; the menu does not.
        if not page.is_descendant(link, url):
            continue
        title = page.plain_text(inner)
        if not title:
            continue
        seen.add(link)
        image = _IMAGE.search(inner)
        poster = page.absolute(image.group(1), BASE) if image else ""
        # Mako lists a long-running programme as its *seasons*, not its
        # episodes: "ארץ נהדרת" answers with twenty-three entries titled
        # "עונה 1" to "עונה 23", each of which is another page listing that
        # season. They were being offered as things to play, so choosing a
        # season asked the player for a directory and nothing happened at all.
        # An episode's address ends in its own VOD document; a season's does
        # not, which is the whole difference.
        if _is_a_season(link):
            found.append(items.new_item(
                "vod",
                ids={"vod": link},
                title=title,
                art={"poster": poster, "thumb": poster},
                extra={"url": router.url_for("vod_show", module="keshet",
                                             ref=link),
                       "module": "keshet", "ref": link},
            ))
            continue
        found.append(items.new_item(
            "vod",
            ids={"vod": link},
            title=title,
            art={"poster": poster},
            extra={"url": router.url_for("play_vod", module="keshet", ref=link),
                   "module": "keshet", "ref": link},
        ))

    if not found:
        stream_url, _adaptive, strategy = page.extract_stream(html, url)
        if stream_url:
            kodi.log("mako: single video page, matched via %s" % strategy)
            found.append(items.new_item(
                "vod", ids={"vod": url}, title=_page_title(html) or "Mako",
                extra={"url": router.url_for("play_vod", module="keshet", ref=url),
                       "module": "keshet", "ref": url}))
    return found


def _is_a_season(link):
    """Is this another listing page rather than one video?

    Every Mako episode is served from its own document, and the address ends
    in it: ".../eretz_nehederet-s20/VOD-5dd3698d83a5381026.htm". A season - or
    any other sub-listing - is a folder address with no document on the end.
    """
    return not _EPISODE_DOCUMENT.match((link or "").rsplit("/", 1)[-1])


def _page_title(html):
    match = re.search(r"<title>(.*?)</title>", html or "", re.S | re.I)
    return page.plain_text(match.group(1)) if match else ""


def stream(ref, mode=""):
    url = page.absolute(ref, BASE)
    html = page.fetch(url, referer=BASE)
    if not html:
        return "", False

    found, adaptive, strategy = page.extract_stream(html, url)
    if not found:
        kodi.log("mako: no strategy matched for %s" % url)
        return "", False

    kodi.log("mako: resolved via %s" % strategy)

    # Mako's VOD CDN refuses an unsigned request exactly as its live one does,
    # so the manifest that was just found is a 403 until it carries a ticket.
    # entitlement.py picks the right vendor from the host: the on-demand
    # streams are on CloudFront and want an AWS token, not the Akamai one the
    # live channels use.
    if not found.startswith("plugin://"):
        from .. import entitlement
        found = entitlement.sign(found, "mako")

    return found, adaptive
