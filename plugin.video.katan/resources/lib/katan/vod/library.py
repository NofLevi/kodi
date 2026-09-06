"""The Israeli VOD catalogue.

A flat index of programmes across the Israeli broadcasters, used for three
things: browsing by channel, feeding the universal search, and supplying the
local half of the search autocomplete.

Entries are stored with short keys because there are a few thousand of them and
they live in the cache. The long names are restored on the way out, so nothing
else in the add-on has to know about the compact form.

    n  name        u  url or id     i  icon
    d  description m  module        o  mode      c  category

The index and its per-broadcaster URL formats were derived from the Idan Plus
add-on by Fishenzon (github.com/Fishenzon/repo), which is where the Kodi POV IL
build gets its Israeli catalogue.
"""
import json
import os

from .. import cache, http, kodi, settings
from ..meta import items

INDEX_TTL = 7 * 24 * 3600
CACHE_KEY = "vod|series"

# Broadcaster labels, so a row heading reads like a channel name.
MODULE_NAMES = {
    "kan": u"\u05db\u05d0\u05df",
    "keshet": u"\u05e7\u05e9\u05ea 12",
    "reshet": u"\u05e8\u05e9\u05ea 13",
    "sport5": u"\u05e1\u05e4\u05d5\u05e8\u05d8 5",
    "sport1": u"\u05e1\u05e4\u05d5\u05e8\u05d8 1",
    "14tv": u"\u05e2\u05e8\u05d5\u05e5 14",
    "891fm": "891FM",
}


def index_url():
    return settings.get("vod.series_url", "").strip()


def seed_path():
    return os.path.join(kodi.addon_path(), "resources", "data", "vod_series.json")


def load():
    """The whole index, from the remote list or the bundled seed."""
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    data = _fetch_remote() or _read_seed()
    if data:
        cache.set(CACHE_KEY, data, INDEX_TTL)
    return data or []


def _fetch_remote():
    url = index_url()
    if not url:
        return None
    payload = http.get_json(url, timeout=(5, 15), default=None)
    if isinstance(payload, list) and payload:
        kodi.log("loaded %d VOD titles from the remote index" % len(payload))
        return payload
    return None


def _read_seed():
    try:
        with open(seed_path(), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        kodi.log("no bundled VOD index available")
        return []


def refresh():
    cache.delete(CACHE_KEY)
    return load()


# --------------------------------------------------------------------------
# converting to items
# --------------------------------------------------------------------------


def _to_item(entry):
    from .. import router

    module = entry.get("m", "")
    icon = entry.get("i", "")
    if icon.startswith("/"):
        # Kan stores relative poster paths against its own host.
        icon = "https://kan.org.il" + icon

    return items.new_item(
        "vod",
        ids={"vod": "%s:%s" % (module, entry.get("u", ""))},
        title=entry.get("n", ""),
        plot=entry.get("d", ""),
        art={"poster": icon, "thumb": icon},
        studio=[MODULE_NAMES.get(module, module)],
        extra={
            "url": router.url_for("vod_show", module=module,
                                  ref=entry.get("u", ""), mode=entry.get("o", "")),
            "module": module,
            "mode": entry.get("o", ""),
            "category": entry.get("c", ""),
            "ref": entry.get("u", ""),
        },
    )


def all_titles():
    """Every programme, used to build the search autocomplete index."""
    return [_to_item(entry) for entry in load()]


def modules():
    """Broadcasters present in the index, with how many programmes each has."""
    counts = {}
    for entry in load():
        module = entry.get("m", "")
        counts[module] = counts.get(module, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def by_module(module, limit=None):
    entries = [e for e in load() if e.get("m") == module]
    entries.sort(key=lambda e: e.get("n", ""))
    result = [_to_item(entry) for entry in entries]
    return result[:limit] if limit else result


def categories(module=None):
    found = {}
    for entry in load():
        if module and entry.get("m") != module:
            continue
        name = entry.get("c") or ""
        if name:
            found[name] = found.get(name, 0) + 1
    return sorted(found.items(), key=lambda kv: -kv[1])


def by_category(name, limit=None):
    entries = [e for e in load() if (e.get("c") or "") == name]
    result = [_to_item(entry) for entry in entries]
    return result[:limit] if limit else result


def search(query, limit=20):
    """Substring search over programme names, Hebrew or Latin.

    Prefix matches come first because they are what someone typing a title
    expects, then anything containing the text.
    """
    needle = (query or "").strip().lower()
    if len(needle) < 2:
        return []

    starts, contains = [], []
    for entry in load():
        name = (entry.get("n") or "").lower()
        if not name:
            continue
        if name.startswith(needle):
            starts.append(entry)
        elif needle in name:
            contains.append(entry)
        if len(starts) >= limit:
            break

    return [_to_item(entry) for entry in (starts + contains)[:limit]]


def newest_episodes(limit=20):
    """A browsable row for the home screen.

    The index carries no air dates, so this is a stable sample across the
    broadcasters rather than a genuine "newest" list, which is why it is
    presented as a catalogue row and not as a recency claim.
    """
    per_module = max(1, limit // max(1, len(MODULE_NAMES)))
    out = []
    for module, _count in modules():
        out.extend(by_module(module, per_module))
        if len(out) >= limit:
            break
    return out[:limit]


def get(module, ref):
    for entry in load():
        if entry.get("m") == module and str(entry.get("u")) == str(ref):
            return _to_item(entry)
    return None
