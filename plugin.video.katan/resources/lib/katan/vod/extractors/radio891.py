"""891FM, the Russian language station, whose archive is a schedule.

This one is not a video catalogue at all. oles.tv publishes a programme page
listing the days a show was broadcast, and each day links into an hourly
archive: the link carries the hour as "?play=15:00" and the archive page
carries a DATA block mapping every hour of that day to an MP3.

So an episode is a (date, hour) pair rather than an id, and resolving one means
reading the day's block and picking the hour out of it. The MP3 is a plain file
with no manifest, which is why this is the only extractor that reports its
stream as not adaptive.

Derived from the Idan Plus add-on by Fishenzon (github.com/Fishenzon/repo).
"""
import json
import re

from ... import kodi, router
from ...meta import items
from . import page

BASE = "https://www.oles.tv"

# The programme page groups its episodes into a block of broadcast days.
_DAY_BLOCK = re.compile(r'class="day">(.*?)class="tools"', re.S)
_EPISODE = re.compile(r'<a href="(.*?)".*?</i>\s*(.*?)</a>', re.S)

# The archive page carries every hour of the day as one JSON object.
_DATA = re.compile(r"DATA\s*=\s*\{(.*?)\};", re.S)

MAX_EPISODES = 120


def episodes(ref, mode=""):
    """The broadcast days of one programme, newest first."""
    url = page.absolute(str(ref or ""), BASE)
    if not url:
        return []

    html = page.fetch(url)
    if not html:
        return []

    blocks = _DAY_BLOCK.search(html)
    if not blocks:
        kodi.log("891fm: no broadcast days on %s" % url)
        return []

    found = []
    seen = set()
    for href, label in _EPISODE.findall(blocks.group(1)):
        link = page.absolute(href.strip(), BASE)
        if not link or link in seen:
            continue
        seen.add(link)
        found.append(_to_item(link, page.plain_text(label)))

    # The page lists oldest first; a listener wants the most recent broadcast.
    found.reverse()
    return found[:MAX_EPISODES]


def _to_item(link, title):
    return items.new_item(
        "vod",
        ids={"vod": link},
        title=title or link.rsplit("/", 1)[-1],
        premiered=_date_of(link),
        extra={"url": router.url_for("play_vod", module="891fm", ref=link),
               "module": "891fm", "ref": link},
    )


def _date_of(link):
    """The archive path carries the broadcast date, so the list can be dated."""
    match = re.search(r"/(\d{4}-\d{2}-\d{2})/", link or "")
    return match.group(1) if match else ""


def stream(ref, mode=""):
    """Resolve one broadcast hour to its MP3."""
    url = page.absolute(str(ref or ""), BASE)
    if not url:
        return "", False

    hour = url.split("?play=")[-1] if "?play=" in url else ""
    html = page.fetch(url, referer=BASE)
    if not html:
        return "", False

    match = _DATA.search(html)
    if not match:
        kodi.log("891fm: no schedule block on %s" % url)
        return "", False

    try:
        schedule = json.loads("{" + match.group(1) + "}")
    except ValueError:
        kodi.log("891fm: the schedule block on %s was not JSON" % url)
        return "", False

    entry = schedule.get(hour)
    if not isinstance(entry, dict):
        kodi.log("891fm: %s is not in the schedule for %s" % (hour or "?", url))
        return "", False

    found = entry.get("stream") or ""
    # An MP3 is a file, not a manifest, so inputstream.adaptive must not claim
    # it. Saying otherwise gives a station that refuses to start.
    return found, False
