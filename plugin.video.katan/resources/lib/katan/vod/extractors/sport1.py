"""Sport 1, whose videos are hosted by Walla rather than by Sport 1.

Three hops, and none of them can be skipped:

    the category page      carries a numeric league id in its pageGlobals
    the wp-json league feed returns the clip cards for that league
    the clip page          embeds a Walla player, and Walla knows the stream

The middle step returns HTML wrapped in JSON, so the escaping is undone before
the cards are read. Clip titles arrive as \\uXXXX sequences even after that, and
they are decoded by handing each one back to the JSON parser as a string rather
than by guessing at an encoding: Hebrew survives that and does not survive
unicode_escape round trips.

Derived from the Idan Plus add-on by Fishenzon (github.com/Fishenzon/repo).
"""
import json
import re

from ... import cache, http, kodi, router
from ...meta import items
from . import page

BASE = "https://sport1.maariv.co.il"
LEAGUE_FEED = (BASE + "/wp-json/sport1/v1/league/%s/video/content"
                      "?is_mobile=false&rows=%d")
WALLA_MEDIA = "https://dal.walla.co.il/media/%s?origin=www.walla.co.il"

LEAGUE_TTL = 3600
MAX_ITEMS = 80

_LEAGUE_ID = re.compile(r'pageGlobals.*?"id":\s*"?(\d+)', re.S)
_CARD = re.compile(r'<div class="video-card(.*?)</a>', re.S)
_CARD_PARTS = re.compile(
    r"""<a href=['"](.*?)['"].*?<img(.*?)>.*?<h3.*?>(.*?)</h3>""", re.S)
_LAZY = re.compile(r'data-lazy="(.*?)"', re.S)
_SRC = re.compile(r"""src=['"](.*?)['"]""", re.S)
_WALLA_ID = re.compile(r'id="walla-iframe-video".*?src=".*?media=(.*?)&', re.S)


def episodes(ref, mode=""):
    url = page.absolute(str(ref or ""), BASE)
    if not url:
        return []

    key = cache.make_key("vod", "sport1", url)
    cached = cache.get(key)
    if cached is None:
        cached = _clips(url)
        if cached:
            cache.set(key, cached, LEAGUE_TTL)
    return [_to_item(clip) for clip in cached or []]


def _clips(url):
    html = page.fetch(url)
    if not html:
        return []

    match = _LEAGUE_ID.search(html)
    if not match:
        kodi.log("sport1: no league id returned by %s" % page._host(url))
        return []

    response = http.get(LEAGUE_FEED % (match.group(1), MAX_ITEMS),
                        headers=page.BROWSER_HEADERS, timeout=(6, 20))
    if response is None or response.status_code >= 400:
        kodi.log("sport1: the league feed did not answer")
        return []

    # The feed is HTML inside JSON, so the escaping comes off first.
    try:
        body = response.text
    except Exception:
        return []
    body = body.replace('\\"', '"').replace("\\/", "/")

    found = []
    for card in _CARD.findall(body):
        parts = _CARD_PARTS.findall(card)
        if not parts:
            continue
        href, image_tag, title = parts[0]
        images = _LAZY.findall(image_tag) or _SRC.findall(image_tag)
        found.append({
            "t": _unescape(title).strip(),
            "u": page.absolute(_unescape(href).strip(), BASE),
            "i": _unescape(images[0]).strip() if images else "",
        })
    return found


def _unescape(text):
    r"""Decode the \uXXXX sequences the feed leaves in its markup.

    Handing the fragment back to the JSON parser is deliberate. The obvious
    alternative, a unicode_escape round trip, mangles Hebrew because it decodes
    through latin-1 first.
    """
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if "\\u" not in cleaned:
        return cleaned
    try:
        return json.loads('"%s"' % cleaned.replace('"', '\\"'))
    except ValueError:
        return cleaned


def _to_item(clip):
    image = clip.get("i") or ""
    return items.new_item(
        "vod",
        ids={"vod": clip.get("u", "")},
        title=clip.get("t") or "Sport 1",
        art={"poster": image, "thumb": image},
        extra={"url": router.url_for("play_vod", module="sport1",
                                     ref=clip.get("u", "")),
               "module": "sport1", "ref": clip.get("u", "")},
    )


def stream(ref, mode=""):
    """Follow the clip page to Walla, which holds the actual stream."""
    url = page.absolute(str(ref or ""), BASE)
    if not url:
        return "", False

    html = page.fetch(url, referer=BASE)
    if not html:
        return "", False

    match = _WALLA_ID.search(html)
    if not match:
        kodi.log("sport1: no Walla player embedded at %s" % page._host(url))
        return "", False

    payload = http.get_json(WALLA_MEDIA % match.group(1),
                            headers=page.BROWSER_HEADERS,
                            timeout=(5, 15), default=None)
    if not isinstance(payload, dict) or payload.get("result") != "success":
        kodi.log("sport1: Walla refused media %s" % match.group(1))
        return "", False

    video = (payload.get("data") or {}).get("video") or {}

    # Walla offers a list of renditions on some clips and a single manifest on
    # others. The last rendition is the highest quality it lists.
    streams = video.get("stream_urls") or []
    if streams:
        found = (streams[-1] or {}).get("stream_url") or ""
        if found:
            return found, True

    found = video.get("url") or ""
    if not found:
        kodi.log("sport1: Walla listed no stream for %s" % match.group(1))
    return found, bool(found)
