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

# **Kan has more than one content tree, and naming them was the mistake.**
# This accepted `/content/kan/` alone. The children's channel is a second
# site, kankids.org.il under `/content/kids/`, with the same CMS and the same
# conventions - 102 catalogue entries under "כאן חינוכית 23" listed nothing
# at all because of it, the whole of the kids channel. Adding that tree then
# turned up a third, `/content/dig/`, where the digital-only programmes live
# ("800 בפסיכומטרי"), and there is no reason to think it is the last.
#
# So the tree is not checked by name any more. What actually keeps the
# station's own "frequencies and channels" article out of every programme is
# `page.is_descendant` below - an episode lives under its programme - and
# that guard never cared which tree it was in.
def _in_a_content_tree(href):
    return "/content/" in (href or "")


# Episode links live under the same programme path and end in a numeric id.
_EPISODE_LINK = re.compile(
    r'<a[^>]+href="((?:https?://[^"]*kan(?:kids)?\.org\.il)?'
    r'/content/[^"]*?/\d+/?)"[^>]*>',
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
        # **A radio programme lists its episodes as players, not as links.**
        # Kan's radio and podcast pages - כאן ב, מורשת, Reka, קול המוסיקה,
        # 88, גימל and תרבות, some 470 catalogue entries between them - carry
        # a `radio-item` per broadcast with the date in its title and the
        # audio in a data attribute, and no page of its own to link to. With
        # nothing here reading those, every one of them fell through to the
        # single-item fallback below and offered the latest broadcast as the
        # whole programme.
        found = _radio_items(html)
        if found:
            kodi.log("kan: %d radio episodes on %s"
                     % (len(found), page._host(url)))

    if not found:
        # Some programmes are a single video rather than a series.
        stream_url, adaptive, strategy = page.extract_stream(html, url)
        if stream_url:
            kodi.log("kan: single video page, matched via %s" % strategy)
            found.append(_direct_item(url, html))

    # **A Kan programme page shows one season and links to the rest**, and
    # only the episodes it shows were ever listed. קופה ראשית has six, its
    # page carries episodes from one, five and six, and the browse screen
    # offered "עונה 1" holding a single entry - the stray "לצפייה" link -
    # beside עונה 5 and עונה 6, with seasons two, three and four nowhere at
    # all. `play._only_this_season` had already been taught to go and fetch
    # a season the page does not list; the screen somebody browses had not.
    #
    # The page says so itself, in a `program-season-tab` anchor per season,
    # so this needs no probing and no extra request. Published the way Mako
    # publishes its seasons - as pages to open rather than things to play -
    # because both the folder builder in `ui.handlers` and the season walk
    # in `play.py` already know that shape and neither needs telling.
    if not _SEASON_IN_URL.search(url):
        seasons = _season_tabs(html, url)
        if len(seasons) > 1:
            kodi.log("kan: %d seasons linked from %s"
                     % (len(seasons), page._host(url)))
            # What belongs to a season now lives in that season's folder.
            # What belongs to none - a trailer, a clip show, "the best of
            # season 5" - is still shown underneath, which is where the
            # programme page itself puts it.
            found = (_season_items(seasons, found)
                     + [item for item in found if not item.get("season")])

    return found


# A season tab on a Kan programme page: one anchor per season, carrying the
# season's own address and its Hebrew name.
_SEASON_TAB = re.compile(
    r'(<a[^>]*class="[^"]*program-season-tab[^"]*"[^>]*>)(.*?)</a>',
    re.S | re.I)
_HREF = re.compile(r'href="([^"]+)"', re.I)


def _season_tabs(html, url):
    """(number, address, name) for every season the page links to."""
    found, seen = [], set()
    for tag, label in _SEASON_TAB.findall(html or ""):
        href = _HREF.search(tag)
        if not href:
            continue
        address = page.absolute(href.group(1), url)
        number = _season_of(address)
        if not number or number in seen:
            continue
        seen.add(number)
        found.append((number, address, page.plain_text(label)))
    return sorted(found)


def _season_items(seasons, episodes_found):
    """One folder per season, to be opened rather than played.

    The season number is deliberately left unset. `ui.handlers._group_folders`
    groups *episodes* into seasons, and these are already seasons - numbering
    them would have it build a folder per folder. Mako's season pages are
    published the same way for the same reason.
    """
    art = {}
    for item in episodes_found:
        art = item.get("art") or {}
        if art.get("poster"):
            break
    return [items.new_item(
        "vod",
        ids={"vod": address},
        title=label or (u"עונה %d" % number),
        art={"poster": art.get("poster", ""), "thumb": art.get("thumb", "")},
        extra={"url": router.url_for("vod_show", module="kan", ref=address),
               "module": "kan", "ref": address},
    ) for number, address, label in seasons]


# Kan puts the season in the address and nowhere else: an episode of "מהצד
# השני" lives at ".../p-12463/s4/1094610/". Without reading it, a programme
# that has run for four seasons is one list of six hundred and twelve.
_SEASON_IN_URL = re.compile(r"/s(\d{1,2})/")


def season_ref(ref, season):
    """Where this programme's season N lives, Kan putting it in the address.

    **A Kan programme page does not list every season.** קופה ראשית has six
    and its page carries episodes from one, five and six; season four is only
    at `.../p-12832/s4/`, which answers perfectly well when asked. Used by
    `play._only_this_season` when the season a viewer pressed is not among
    the entries the programme page gave.
    """
    base = (ref or "").split("?")[0].rstrip("/")
    if not base or not season:
        return ""
    # Already a season address: swap the number rather than nesting another.
    base = _SEASON_IN_URL.sub("/", base + "/").rstrip("/")
    return "%s/s%d/" % (base, int(season))


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


# A reference that is already the media, so there is nothing to scrape.
_MEDIA_REF = re.compile(r"\.(?:mp3|m4a|aac|mp4|m3u8)(?:\?|$)", re.I)
# One broadcast on a radio programme page. The source is on the play button
# and the name is the heading beside it; both are read from the same block so
# a page of twenty cannot pair the wrong title with the wrong audio.
_RADIO_ITEM = re.compile(
    r'data-player-title="([^"]{2,160})"\s*[^>]*?data-player-src="([^"]+)"',
    re.I | re.S)
# Kan titles a radio broadcast by date: "14.11.2026-יומן השבוע".
_RADIO_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")


def _radio_items(html):
    """Every broadcast a radio or podcast page offers, newest first."""
    found, seen = [], set()
    for title, source in _RADIO_ITEM.findall(html or ""):
        src = page.absolute(source, BASE)
        if not src or src in seen:
            continue
        seen.add(src)
        name = page.plain_text(title)
        stamp = _RADIO_DATE.search(name)
        found.append(items.new_item(
            "vod",
            ids={"vod": src},
            title=name or "Kan",
            # The date Kan puts in the title, so Kodi can sort and show it
            # rather than leaving the viewer to read it out of the name.
            premiered=("%s-%s-%s" % (stamp.group(3), stamp.group(2).zfill(2),
                                     stamp.group(1).zfill(2))
                       if stamp else ""),
            extra={"url": router.url_for("play_vod", module="kan", ref=src),
                   "module": "kan", "ref": src},
        ))
    return found


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

    # A radio episode's reference *is* the audio file - see `_radio_items` -
    # so there is no page to fetch, and fetching one would download the
    # programme instead of a document.
    if _MEDIA_REF.search(url):
        return url, False

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
