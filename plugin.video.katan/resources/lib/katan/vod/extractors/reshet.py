"""Reshet 13, whose catalogue lives in Kaltura OTT rather than on the website.

reshet.tv answers anything that does not look like a real browser session with
a 403, so scraping the site the way kan.py and mako.py do is not a route. Its
own apps do not use the website either: they talk to Kaltura's OTT API, which
grants an anonymous session to anyone who asks and needs no account, no key and
no login.

Three calls, in order:

    ottUser/anonymousLogin      a Kaltura session, good for about a day
    asset/list                  the episodes belonging to one SeriesID
    asset/getPlaybackContext    the HLS manifest for one episode

The bundled catalogue already stores Reshet programmes by their Kaltura
SeriesID, so a programme reference needs no translation. That was checked
rather than assumed: all 144 bundled ids resolve against the live API and the
names match exactly.

One caveat worth stating plainly. getPlaybackContext advertises a FairPlay
licence alongside the manifest, because the same catalogue feeds Apple devices.
The manifest this module asks for carries no EXT-X-KEY and its segments are
served in the clear, so inputstream.adaptive has everything it needs, but no
episode has been played end to end in a real Kodi yet. If a programme turns out
to be genuinely protected it will fail at the player rather than here.
"""
import re

from ... import cache, http, kodi, router
from ...meta import items
from .. import kaltura

API = "https://api.frp1.ott.kaltura.com/api_v3"
PARTNER = 5031

# Not the same partner, and not interchangeable. 5031 is Reshet's *OTT*
# account, which is the layer that inserts the adverts; 2748741 is the plain
# Kaltura account the broadcaster's own website plays from. Passing the OTT id
# to cdnapisec resolves nothing at all.
WEB_PARTNER = 2748741
SITE = "https://13tv.co.il"
API_VERSION = "5.4.0"

# Kaltura calls the shapes of its catalogue "asset structs". These ids are
# per-partner, and they were read from assetStruct/list rather than guessed.
TYPE_SERIES = 1259
TYPE_EPISODE = 1268

# The session the API hands back expires in about a day. Twelve hours keeps a
# comfortable margin while still costing at most two logins a day.
SESSION_TTL = 12 * 3600

# How many episodes one programme may list. Reshet's longest runs to several
# hundred, which is more rows than anyone scrolls and more artwork than a
# low-memory device should hold at once.
MAX_EPISODES = 120

_HEADERS = {"Content-Type": "application/json",
            "Accept": "application/json"}

# Reshet titles its episodes "<programme>, season N, episode M: <name>" in
# Hebrew, and that text is the only trustworthy source of the numbering. The
# SeasonNumber and EpisodeNumber metadata fields look right but are not: an
# episode titled season 1 episode 1 reports SeasonNumber 5 and EpisodeNumber
# 12, and within one season the numbers run backwards. They appear to be
# internal counters, so the title wins and the metadata is only a fallback.
#
#   עונה = "season", פרק = "episode"
_SEASON_EPISODE = re.compile(
    u"עונה\\s*(\\d+)\\s*,?\\s*"
    u"פרק\\s*(\\d+)")
_EPISODE_ONLY = re.compile(u"פרק\\s*(\\d+)")

# "MM:SS", occasionally "00:00" when the broadcaster did not fill it in.
_RUNTIME = re.compile(r"^(\d+):(\d{2})$")


def _call(service, action, payload, timeout=(5, 12)):
    """One Kaltura OTT call, returning the result or None."""
    body = dict(payload)
    body["apiVersion"] = API_VERSION
    response = http.post_json(
        "%s/service/%s/action/%s" % (API, service, action),
        json=body, headers=_HEADERS, timeout=timeout, default=None)

    if not isinstance(response, dict):
        kodi.log("reshet: %s/%s did not answer" % (service, action))
        return None

    result = response.get("result", response)
    if isinstance(result, dict) and "error" in result:
        kodi.log("reshet: %s/%s refused: %s"
                 % (service, action, (result["error"] or {}).get("message")))
        return None
    return result


def session(refresh=False):
    """An anonymous Kaltura session, cached because it lasts a day."""
    key = cache.make_key("vod", "reshet", "ks")
    if not refresh:
        cached = cache.get(key)
        if cached:
            return cached

    result = _call("ottUser", "anonymousLogin",
                   {"partnerId": PARTNER, "udid": UDID})
    ks = (result or {}).get("ks", "")
    if ks:
        cache.set(key, ks, SESSION_TTL)
    return ks


# The API wants a device id but does not check it for an anonymous session, so
# this is a constant rather than a fingerprint. Generating a per-install id
# would identify the device to the broadcaster for no benefit to the viewer.
UDID = "katan-kodi"


# --------------------------------------------------------------------------
# listing
# --------------------------------------------------------------------------


def episodes(ref, mode=""):
    """Every episode of one programme, newest first."""
    series_id = str(ref or "").strip()
    if not series_id:
        return []

    ks = session()
    if not ks:
        return []

    result = _call("asset", "list", {
        "ks": ks,
        "filter": {"objectType": "KalturaSearchAssetFilter",
                   "typeIn": str(TYPE_EPISODE),
                   "kSql": "(and SeriesID='%s')" % series_id,
                   "orderBy": "START_DATE_DESC"},
        "pager": {"objectType": "KalturaFilterPager",
                  "pageSize": MAX_EPISODES, "pageIndex": 1},
    })

    assets = (result or {}).get("objects") or []
    if not assets:
        kodi.log("reshet: series %s listed no episodes" % series_id)

    found = [_to_item(asset) for asset in assets]
    # The API orders by a start date that is identical for every episode of a
    # programme, so it arrives effectively unsorted. Season and episode give
    # the order a viewer expects, and it is what picking the next unwatched
    # episode depends on.
    found.sort(key=lambda item: (item["season"], item["episode"],
                                 item["title"]))
    return found


def _to_item(asset):
    asset_id = str(asset.get("id") or "")
    metas = asset.get("metas") or {}
    image = _image(asset)
    name = asset.get("name") or asset_id
    season, episode = _season_episode(name, metas)

    return items.new_item(
        "vod",
        ids={"vod": asset_id},
        title=_episode_title(name),
        plot=_meta(metas, "LongSummary") or _meta(metas, "ShortSummary") or "",
        art={"poster": image, "thumb": image},
        season=season,
        episode=episode,
        duration=_runtime(_meta(metas, "RunTime")),
        extra={"url": router.url_for("play_vod", module="reshet", ref=asset_id),
               "module": "reshet", "ref": asset_id},
    )


def _season_episode(name, metas):
    """Season and episode, read from the title and only then from metadata."""
    match = _SEASON_EPISODE.search(name or "")
    if match:
        return int(match.group(1)), int(match.group(2))

    # A programme with no seasons still numbers its episodes.
    match = _EPISODE_ONLY.search(name or "")
    if match:
        return 1, int(match.group(1))

    return _number(_meta(metas, "SeasonNumber")), \
        _number(_meta(metas, "EpisodeNumber"))


def _episode_title(name):
    """Drop the programme and numbering prefix, keeping the episode's own name.

    "Programme, season 2, episode 4: The big one" becomes "The big one". The
    row already sits under the programme and shows its numbering, so repeating
    both wastes the width a Hebrew title needs.
    """
    text = name or ""
    head, sep, tail = text.partition(":")
    if sep and _EPISODE_ONLY.search(head):
        cleaned = tail.strip()
        if cleaned:
            return cleaned
    return text.strip()


def _meta(metas, name):
    entry = metas.get(name)
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _number(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _runtime(value):
    """Kaltura reports a running time as "MM:SS"; Kodi wants seconds."""
    match = _RUNTIME.match(str(value or "").strip())
    if not match:
        return 0
    return int(match.group(1)) * 60 + int(match.group(2))


def _image(asset):
    """The largest image the asset offers, which is still a modest thumbnail."""
    images = asset.get("images") or []
    for image in images:
        url = (image or {}).get("url") or ""
        if url:
            return url
    return ""


# --------------------------------------------------------------------------
# playback
# --------------------------------------------------------------------------


def stream(ref, mode=""):
    """Resolve one episode to an HLS manifest."""
    asset_id = str(ref or "").strip()
    if not asset_id:
        return "", False

    ks = session()
    if not ks:
        return "", False

    result = _call("asset", "getPlaybackContext", {
        "ks": ks,
        "assetId": asset_id,
        "assetType": "media",
        "contextDataParams": {"objectType": "KalturaPlaybackContextOptions",
                              "context": "PLAYBACK",
                              "streamerType": "applehttp",
                              "mediaProtocol": "https"},
    })

    sources = (result or {}).get("sources") or []
    if not sources:
        kodi.log("reshet: no playable source for asset %s" % asset_id)
        return "", False

    # Prefer a source with no DRM attached. Reshet offers FairPlay on the same
    # asset for Apple clients, and picking a clear one when there is a choice
    # costs nothing and avoids a stream Kodi cannot open.
    clear = [s for s in sources if not s.get("drm") and s.get("url")]

    # Every one of these URLs is server-side ad insertion: the OTT endpoint
    # answers with hub13.g-mana.live, which splices adverts into the same HLS
    # timeline as the programme. Measured on one episode, the player showed
    # fifteen chapters and opened on an advert. The entry underneath is on
    # plain Kaltura with nothing inserted, and its id is already here - the
    # source's externalId is `<entry>_<flavour>` - so this costs one request
    # and no change to how anything is listed.
    for source in clear or sources:
        entry = kaltura.entry_id(source.get("externalId"))
        if not entry:
            continue
        url, adaptive = kaltura.playback_url(entry, WEB_PARTNER, SITE)
        if url:
            kodi.log("reshet: %s without the ad insertion" % entry)
            return url, adaptive
        break

    if clear:
        return clear[0]["url"], True

    url = sources[0].get("url") or ""
    if url:
        kodi.log("reshet: only a DRM source was offered for %s" % asset_id)
    return url, bool(url)
