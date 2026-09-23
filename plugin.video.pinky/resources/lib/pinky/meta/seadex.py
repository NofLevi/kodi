"""SeaDex, which knows which anime release is actually the good one.

Anime is the one case where picking by resolution and seeders reliably gets it
wrong. Two 1080p releases of the same episode can differ by a botched
encode, missing honorifics, hardsubbed signs or the wrong audio track, and
nothing in a release name says which is which. The community answer is SeaDex
(releases.moe), a curated table of which group's release is best for a given
title, maintained by people who compare them frame by frame.

It is a PocketBase instance with an open read API: no key, no account, and one
request answers a whole title. Entries are keyed by AniList id, which the
add-on already carries for anime, so nothing has to be matched by name.

What comes back is a list of releases with their infohashes. Since the source
aggregator merges everything by infohash already, a SeaDex recommendation is
just a set of hashes to recognise - no name matching, no fuzzy comparison, and
no risk of promoting the wrong file. A title with no SeaDex entry scores
exactly as it did before.
"""
from .. import cache, http, kodi, settings

API = "https://releases.moe/api/collections/entries/records"

# The table is curated by hand and changes slowly, so a day is conservative.
TTL = 24 * 3600
# A negative answer is cached too, and for less time, so that an anime SeaDex
# has not covered yet does not cost a request on every single search.
MISS_TTL = 6 * 3600

TIMEOUT = (4, 8)


def enabled():
    return settings.get_bool("sources.seadex", True)


def best_hashes(anilist_id):
    """Infohashes SeaDex marks as the best release for this title.

    Returns a set, empty when the title is unknown, the feature is off or the
    service does not answer. Every failure is the same as "no opinion", which
    is what keeps this safe to consult on every anime search.
    """
    if not anilist_id or not enabled():
        return set()

    key = cache.make_key("seadex", anilist_id)
    cached = cache.get(key)
    if cached is not None:
        return set(cached)

    hashes = _fetch(anilist_id)
    cache.set(key, sorted(hashes), TTL if hashes else MISS_TTL)
    return hashes


def _fetch(anilist_id):
    payload = http.get_json(
        API,
        params={"filter": "(alID=%s)" % anilist_id,
                "expand": "trs",
                "perPage": 1},
        timeout=TIMEOUT,
        default=None)

    if not isinstance(payload, dict):
        kodi.log("seadex did not answer for anilist %s" % anilist_id)
        return set()

    entries = payload.get("items")
    if not isinstance(entries, list) or not entries:
        return set()

    found = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        releases = (entry.get("expand") or {}).get("trs")
        if not isinstance(releases, list):
            continue
        for item in releases:
            if not isinstance(item, dict) or not item.get("isBest"):
                continue
            digest = str(item.get("infoHash") or "").strip().lower()
            # SeaDex uses a placeholder on releases that are not torrents, and
            # a hash that is not 40 hex characters can never match a source.
            if len(digest) == 40 and _is_hex(digest):
                found.add(digest)

    if found:
        kodi.log("seadex knows %d preferred releases for anilist %s"
                 % (len(found), anilist_id))
    return found


def _is_hex(text):
    try:
        int(text, 16)
        return True
    except ValueError:
        return False
