"""Sport 5, which publishes its whole catalogue as two files.

There is no per-programme API to call. Sport 5 exposes one JSON document
holding every video category and every clip in it, and a second one holding the
radio archive. That is convenient and also the problem: the video index is
1.5 MB and the radio index 4.5 MB, which is not something to parse on a one
gigabyte box more often than necessary.

So neither document is cached as it arrives. Each is reduced on the way in to
the few fields the add-on actually shows - a title, a picture, a plot and a
URL - and only the reduced form is stored: 75 categories in about 500 KB, and
5,279 radio episodes in about 3 MB, both compressed again by the cache itself.

The radio index stays the largest single thing this add-on keeps, and that is a
deliberate trade rather than an oversight. Reducing it further would mean
dropping the episode URLs, which are more than half its weight and the only
part that cannot be recomputed. Holding it means one parse every twelve hours
instead of a 4.5 MB download and parse every time somebody opens a programme,
which is the cost that would actually be felt.

Two kinds of entry live under this broadcaster, and the catalogue's mode says
which:

    mode 1 (or empty)   a video category id, whose items are clips
    mode 22             a radio episode, identified by its guid

A clip's stream_url is a watch.sport5.co.il player page with the real manifest
in its src parameter, so the manifest is lifted out rather than handing Kodi a
web page. Some clips carry "about:blank;" there and a working link in
stream_url_bak, which is why both are tried.

Derived from the Idan Plus add-on by Fishenzon (github.com/Fishenzon/repo).
"""
from ... import cache, http, kodi, router
from ...meta import items

VOD_INDEX = "https://vod.sport5.co.il/HTML/External/VodCentertDS.txt"
RADIO_INDEX = "https://radio.sport5.co.il/data/data.json"
VOD_REFERER = "https://vod.sport5.co.il"
RADIO_REFERER = "https://radio.sport5.co.il"

# Sport news turns over fast, so the video index is refreshed hourly, which is
# what the broadcaster's own client does. The radio archive is historical and
# barely moves, so it is kept far longer.
VOD_TTL = 3600
RADIO_TTL = 12 * 3600

# The radio archive holds over five thousand episodes. Keeping only the ones
# reachable from the catalogue would couple this module to the bundled data, so
# all of them are kept, but reduced to four fields each.
MAX_ITEMS = 200

MODE_RADIO = "22"


# --------------------------------------------------------------------------
# the two indexes, reduced before they are cached
# --------------------------------------------------------------------------


def _fetch_json(url, referer):
    """Fetch and decode one of the two index documents.

    The bytes are decoded here rather than trusting the response, because the
    video index is served as text/plain with no charset and a byte order mark.
    requests reads a charset-less text/plain as ISO-8859-1, which turns the
    mark into two stray characters and the Hebrew into nonsense, so the answer
    parses as neither JSON nor Hebrew. Decoding as utf-8-sig strips the mark
    and is correct whichever HTTP backend is installed.
    """
    import json

    response = http.get(url, headers={"Referer": referer}, timeout=(6, 25))
    if response is None or response.status_code >= 400:
        kodi.log("sport5: %s did not answer" % http._host(url))
        return None

    try:
        text = response.content.decode("utf-8-sig", "replace")
    except Exception:
        kodi.log("sport5: could not decode response from %s" % http._host(url))
        return None

    try:
        return json.loads(text)
    except ValueError:
        kodi.log("sport5: response from %s was not JSON" % http._host(url))
        return None


def video_index(refresh=False):
    """Category id -> a small list of clips."""
    key = cache.make_key("vod", "sport5", "videos")
    if not refresh:
        cached = cache.get(key)
        if cached is not None:
            return cached

    payload = _fetch_json(VOD_INDEX, VOD_REFERER)
    reduced = _reduce_categories(payload)
    if reduced:
        cache.set(key, reduced, VOD_TTL)
    return reduced


def _reduce_categories(payload):
    categories = ((payload or {}).get("Category") or {}).get("Category")
    if not categories:
        return {}
    if isinstance(categories, dict):
        categories = [categories]

    index = {}
    for category in categories:
        if not isinstance(category, dict):
            continue
        key = str(category.get("ID") or "")
        if not key:
            continue
        index[key] = _reduce_items(_walk(category))
    return index


def _walk(node):
    """Every clip under a category, including the ones inside its seasons."""
    found = []
    entries = (node.get("Items") or {}).get("Item")
    if entries:
        found.extend([entries] if isinstance(entries, dict) else entries)

    children = node.get("Category")
    if children:
        for child in ([children] if isinstance(children, dict) else children):
            if isinstance(child, dict):
                found.extend(_walk(child))
    return found


def _reduce_items(entries):
    reduced = []
    for entry in entries[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        url = _manifest_of(entry)
        if not url:
            continue
        reduced.append({
            "t": entry.get("title") or "",
            "u": url,
            "i": entry.get("img_upload") or entry.get("img") or "",
            "d": entry.get("abstract") or "",
        })
    return reduced


def _manifest_of(entry):
    """The real manifest behind a clip, or an empty string.

    stream_url is a player page carrying the manifest in its src parameter.
    When the broadcaster has not filled it in it reads "about:blank;", and
    stream_url_bak holds the working link instead.
    """
    for field in ("stream_url", "stream_url_bak"):
        candidate = str(entry.get(field) or "")
        if candidate.startswith("http"):
            found = unwrap(candidate)
            if found:
                return found
    return ""


def unwrap(url):
    """Pull the manifest out of a watch.sport5.co.il player URL."""
    if not url.startswith("http"):
        return ""
    try:
        from urllib.parse import parse_qs, urlparse
    except ImportError:      # pragma: no cover
        from urlparse import parse_qs, urlparse

    query = parse_qs(urlparse(url).query)
    for name in ("src", "videoUrl"):
        values = query.get(name)
        if values and values[0].startswith("http"):
            return values[0]
    return url


def radio_index(refresh=False):
    """Episode guid -> its title, picture and stream."""
    key = cache.make_key("vod", "sport5", "radio")
    if not refresh:
        cached = cache.get(key)
        if cached is not None:
            return cached

    payload = _fetch_json(RADIO_INDEX, RADIO_REFERER)
    reduced = _reduce_radio(payload)
    if reduced:
        cache.set(key, reduced, RADIO_TTL)
    return reduced


def _reduce_radio(payload):
    nodes = (payload or {}).get("data")
    if not isinstance(nodes, dict):
        return {}

    index = {}
    for key, node in nodes.items():
        if not isinstance(node, dict) or node.get("type") == "folder":
            continue
        url = str(node.get("url") or "").replace(u"‏", "").strip()
        if not url.startswith("http"):
            continue
        index[str(key)] = {
            "t": node.get("name") or "",
            "u": url,
            "i": node.get("imageUrl") or "",
            "d": node.get("description") or "",
            "a": node.get("time") or "",
        }
    return index


# --------------------------------------------------------------------------
# listing
# --------------------------------------------------------------------------


def episodes(ref, mode=""):
    reference = str(ref or "").strip()
    if not reference:
        return []

    if str(mode) == MODE_RADIO:
        entry = radio_index().get(reference)
        if not entry:
            kodi.log("sport5: no radio episode %s" % reference)
            return []
        return [_radio_item(reference, entry)]

    clips = video_index().get(reference)
    if not clips:
        kodi.log("sport5: category %s listed no clips" % reference)
        return []
    return [_video_item(clip) for clip in clips]


def _video_item(clip):
    image = clip.get("i") or ""
    return items.new_item(
        "vod",
        ids={"vod": clip.get("u", "")},
        title=clip.get("t") or "Sport 5",
        plot=clip.get("d") or "",
        art={"poster": image, "thumb": image},
        extra={"url": router.url_for("play_vod", module="sport5",
                                     ref=clip.get("u", "")),
               "module": "sport5", "ref": clip.get("u", "")},
    )


def _radio_item(guid, entry):
    image = entry.get("i") or ""
    return items.new_item(
        "vod",
        ids={"vod": guid},
        title=entry.get("t") or "Sport 5",
        plot=entry.get("d") or "",
        art={"poster": image, "thumb": image},
        premiered=_date(entry.get("a")),
        extra={"url": router.url_for("play_vod", module="sport5", ref=guid,
                                     mode=MODE_RADIO),
               "module": "sport5", "ref": guid, "mode": MODE_RADIO},
    )


def _date(stamp):
    """The archive stamps an episode "YYYY/MM/DD HH:MM"; Kodi wants a date."""
    text = str(stamp or "").split(" ")[0].replace("/", "-")
    parts = text.split("-")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return "%s-%s-%s" % (parts[0], parts[1].zfill(2), parts[2].zfill(2))
    return ""


# --------------------------------------------------------------------------
# playback
# --------------------------------------------------------------------------


def stream(ref, mode=""):
    reference = str(ref or "").strip()
    if not reference:
        return "", False

    if reference.startswith("http"):
        return unwrap(reference), True

    entry = radio_index().get(reference)
    if not entry:
        kodi.log("sport5: nothing playable for %s" % reference)
        return "", False

    # The archive stores a delivery path; the manifest sits underneath it.
    url = entry["u"]
    if not url.endswith(".m3u8"):
        url = url.rstrip("/") + "/master.m3u8"
    return url, True
