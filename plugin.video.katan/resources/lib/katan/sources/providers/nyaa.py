"""Nyaa, the main public index for fansubbed anime.

Nyaa has no JSON API, but its RSS feed carries everything needed: title, size,
seeders and the infohash. Parsing RSS costs nothing compared with scraping the
HTML, and it is far less likely to break.
"""
import re

from ... import http, kodi
from ...sources import model

NAME = "nyaa"
BASE = "https://nyaa.si"

# 1_2 is "English translated anime", which is what a Kodi user wants.
CATEGORY = "1_2"

_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(GiB|MiB|GB|MB)", re.I)


def search(meta):
    query = _query_for(meta)
    if not query:
        return []
    response = http.get("%s/?page=rss&c=%s&f=0&q=%s" % (BASE, CATEGORY, _quote(query)),
                        timeout=(4, 8))
    if response is None or response.status_code != 200:
        return []
    return _parse_rss(response.text, meta)


def _query_for(meta):
    title = meta.get("original_title") or meta.get("title") or ""
    if not title:
        return ""
    if meta.get("type") == "episode":
        # Fansub groups number anime episodes absolutely far more often than
        # by season, so the episode number alone is the better query.
        return "%s %02d" % (title, int(meta.get("episode") or 1))
    return title


def _quote(text):
    try:
        from urllib.parse import quote_plus
    except ImportError:      # pragma: no cover
        from urllib import quote_plus
    return quote_plus(text)


def _parse_rss(text, meta):
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(text.encode("utf-8"))
    except Exception:
        kodi.log("nyaa returned unparseable RSS")
        return []

    namespace = {"nyaa": "https://nyaa.si/xmlns/nyaa"}
    sources = []
    for item in root.iter("item"):
        title = _text(item, "title")
        info_hash = _text(item, "nyaa:infoHash", namespace)
        if not title or not info_hash:
            continue
        sources.append(model.from_release_name(
            title, provider=NAME,
            size=_size_bytes(_text(item, "nyaa:size", namespace)),
            seeders=_int(_text(item, "nyaa:seeders", namespace)),
            info_hash=info_hash))
    return sources


def _text(item, tag, namespace=None):
    node = item.find(tag, namespace) if namespace else item.find(tag)
    return (node.text or "").strip() if node is not None and node.text else ""


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _size_bytes(value):
    match = _SIZE.search(value or "")
    if not match:
        return 0
    number = float(match.group(1))
    unit = match.group(2).lower()
    factor = {"mib": 1024 ** 2, "mb": 1024 ** 2,
              "gib": 1024 ** 3, "gb": 1024 ** 3}.get(unit, 0)
    return int(number * factor)
