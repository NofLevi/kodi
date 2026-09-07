"""Nyaa, the main public index for fansubbed anime.

Nyaa has no JSON API, but its RSS feed carries everything needed: title, size,
seeders and the infohash. Parsing RSS costs nothing compared with scraping the
HTML, and it is far less likely to break.
"""
import re

from ... import http, kodi
from ...sources import model
from ...utils import release

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
    """The name and number this index is actually going to match on.

    `search_title` is the English one, set for anime by play.build_meta,
    because the original title is Japanese and this searches release names as
    text: the Japanese title returns zero results, every time, for every
    anime tried.

    `absolute` is the episode counted from the first rather than from the
    season, because fansub groups number that way. Both fall back to what was
    used before when they are absent, so nothing else changes.
    """
    title = (meta.get("search_title") or meta.get("original_title")
             or meta.get("title") or "")
    if not title:
        return ""
    if meta.get("type") == "episode":
        number = int(meta.get("absolute") or meta.get("episode") or 1)
        return "%s %02d" % (title, number)
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
    wanted = _episode_filter(meta)
    sources = []
    for item in root.iter("item"):
        title = _text(item, "title")
        info_hash = _text(item, "nyaa:infoHash", namespace)
        if not title or not info_hash:
            continue
        if wanted and not wanted(title):
            continue
        sources.append(model.from_release_name(
            title, provider=NAME,
            size=_size_bytes(_text(item, "nyaa:size", namespace)),
            seeders=_int(_text(item, "nyaa:seeders", namespace)),
            info_hash=info_hash))
    return sources


def _episode_filter(meta):
    """Refuse the episodes this is not, because Nyaa will offer them.

    Nyaa searches the release name as text and matches loosely, so asking for
    episode 14 returns forty-five results for episode *149*. Every other
    provider is asked by IMDb id and is right by construction; this one has to
    check, and until now it did not - so a search that found nothing was the
    better outcome, and a search that found something offered a different
    episode of the right show with every appearance of confidence.

    A release whose name carries no number at all is kept. Fansub batches are
    routinely named "Complete Series" with the range only in the file list,
    and the debrid layer picks the right file out of a pack anyway - so an
    unnumbered name is an unknown rather than a wrong answer, and refusing it
    would throw away the packs that are often all anybody is seeding.
    """
    if (meta or {}).get("type") != "episode":
        return None
    season = int(meta.get("season") or 1)
    episode = int(meta.get("episode") or 0)
    absolute = int(meta.get("absolute") or 0) or episode
    if not episode:
        return None

    def keep(name):
        parsed = release.parse(name)
        if not (parsed["season"] or parsed["episode"] or parsed["absolute"]
                or parsed.get("episode_range")):
            return True                     # says nothing; let it through
        return release.matches_episode(parsed, season, episode, absolute)
    return keep


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
