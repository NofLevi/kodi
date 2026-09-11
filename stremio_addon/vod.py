# -*- coding: utf-8 -*-
"""Katan's Israeli VOD and live channels as Stremio catalogues.

Ids carry everything needed to answer later without a lookup table:

    kv:<module>:<mode>:<ref>     a programme (meta) or an episode (stream)
    kl:<channel id>              a live channel

<ref> is URL-safe base64, because Katan's refs are URLs and Stremio ids are
path segments.
"""
import base64
import collections
import re

try:
    from urllib.parse import parse_qs, urlparse
except ImportError:                                   # pragma: no cover
    from urlparse import parse_qs, urlparse

PAGE = 100
NO_MODE = "-"
RADIO_MODE = "22"                 # sport5's radio archive
RADIO_MODULES = ("891fm",)
# Stremio needs a date on every episode; one this old never reads as upcoming.
UNDATED = "2000-01-01T00:00:00.000Z"
META_TTL = 1800
MAX_SEASON_FOLDERS = 15

# Now 14's CDN is fed from its web player and wants to see it as the Referer.
PLAYER_HEADERS = {
    "14tv": {"Referer": "https://vod.c14.co.il/",
             "Origin": "https://vod.c14.co.il"},
}


# --------------------------------------------------------------------------
# ids
# --------------------------------------------------------------------------


def _token(text):
    raw = base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")
    return raw.rstrip("=")


def _untoken(token):
    padded = token + "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


def vod_id(module, mode, ref):
    return "kv:%s:%s:%s" % (module, mode or NO_MODE, _token(ref))


def live_id(channel_id):
    return "kl:%s" % channel_id


def parse_id(item_id):
    """("vod", module, mode, ref) or ("live", channel, "", "") or None."""
    parts = (item_id or "").split(":")
    try:
        if parts[0] == "kv" and len(parts) == 4:
            # base64 quietly drops characters outside its alphabet, so a
            # mangled id would decode to an empty ref rather than fail.
            if not re.match(r"^[A-Za-z0-9_-]+$", parts[3]):
                return None
            ref = _untoken(parts[3])
            if not ref:
                return None
            mode = "" if parts[2] == NO_MODE else parts[2]
            return "vod", parts[1], mode, ref
        if parts[0] == "kl" and len(parts) == 2 and parts[1]:
            return "live", parts[1], "", ""
    except (ValueError, UnicodeDecodeError):
        return None
    return None


# --------------------------------------------------------------------------
# catalogues
# --------------------------------------------------------------------------


def catalogs():
    """The manifest's catalogue list: one per broadcaster, radio, live, search."""
    from katan.vod import library

    found = []
    for module, _count in library.modules():
        if module in RADIO_MODULES:
            continue
        entry = {"type": "series", "id": "kv-" + module,
                 "name": library.MODULE_NAMES.get(module, module),
                 "extra": [{"name": "skip"}]}
        counts = collections.Counter(
            (i.get("extra") or {}).get("category") for i in _module_items(module))
        names = [name for name, _n in counts.most_common() if name]
        if len(names) > 1:
            entry["extra"].append({"name": "genre", "options": names})
        found.append(entry)
    found.append({"type": "series", "id": "kv-radio", "name": "רדיו",
                  "extra": [{"name": "skip"}]})
    found.append({"type": "series", "id": "kv-search", "name": "VOD ישראלי",
                  "extra": [{"name": "search", "isRequired": True}]})
    found.append({"type": "tv", "id": "kl-live", "name": "ערוצים בשידור חי",
                  "extra": [{"name": "skip"}]})
    return found


def catalog(kind, catalog_id, extra):
    extra = extra or {}
    skip = _int(extra.get("skip"))
    if catalog_id == "kl-live":
        return [_channel_preview(c) for c in _live()][skip:skip + PAGE]
    items = _catalog_items(catalog_id, extra)
    return [_preview(item) for item in items[skip:skip + PAGE]]


def _catalog_items(catalog_id, extra):
    from katan.vod import library

    if catalog_id == "kv-search":
        query = (extra.get("search") or "").strip()
        if not query:
            return []
        return [i for i in library.search(query, limit=PAGE * 2)
                if playable(i)][:PAGE]
    if catalog_id == "kv-radio":
        radio = [i for i in library.by_module("sport5")
                 if (i.get("extra") or {}).get("mode") == RADIO_MODE]
        for module in RADIO_MODULES:
            radio.extend(library.by_module(module))
        return radio
    if not catalog_id.startswith("kv-"):
        return []
    module = catalog_id[len("kv-"):]
    genre = extra.get("genre")
    return [i for i in _module_items(module)
            if not genre or (i.get("extra") or {}).get("category") == genre]


# Kan's programme index also holds its radio stations and podcasts, and
# Katan's Kan extractor plays neither - measured: a podcast episode matches no
# strategy, a radio programme lists nothing or answers 404. Offering them
# would fill the catalogue with tiles that never play, so only Kan's TV is.
UNPLAYABLE = {("kan", "23"), ("kan", "32"), ("kan", "34")}
# Kan Educational (102 programmes) lives on kankids.org.il, which Katan's Kan
# extractor does not read: every one sampled listed no episodes at all.
UNPLAYABLE_SITES = ("kankids.org.il",)


def playable(item):
    extra = item.get("extra") or {}
    if (extra.get("module"), extra.get("mode")) in UNPLAYABLE:
        return False
    return not any(site in (extra.get("ref") or "") for site in UNPLAYABLE_SITES)


def _module_items(module):
    """A broadcaster's programmes that play, radio left to its own catalogue."""
    from katan.vod import library

    return [i for i in library.by_module(module)
            if playable(i) and (i.get("extra") or {}).get("mode") != RADIO_MODE]


def _preview(item):
    extra = item.get("extra") or {}
    preview = {
        "id": vod_id(extra.get("module", ""), extra.get("mode", ""),
                     extra.get("ref", "")),
        "type": "series",
        "name": display_name(item),
        "poster": _usable(item.get("art") or {}),
        "posterShape": "poster",
        "description": item.get("plot") or "",
    }
    if extra.get("category"):
        preview["genres"] = [extra["category"]]
    return preview


def display_name(item):
    """The programme's name, or the opening of its description.

    Six of Kan's programmes - the evening news among them - have a name made
    only of whitespace in the library, and Stremio would show a blank tile.
    Their descriptions open with what the name should have been:
    "חדשות הערב - כל ערב בשעה 20:00."
    """
    title = (item.get("title") or "").strip()
    if title:
        return title
    plot = (item.get("plot") or "").strip()
    opening = re.split(r"\s+-\s+|[.!?\n]", plot, 1)[0].strip()
    return opening[:60] or (item.get("extra") or {}).get("category") or ""


def _usable(art):
    """A poster Stremio can load. Some of Katan's live only inside Kodi."""
    for key in ("poster", "thumb", "fanart"):
        url = art.get(key) or ""
        if url.startswith(("http://", "https://")):
            return url
    return None


# --------------------------------------------------------------------------
# a programme and its episodes
# --------------------------------------------------------------------------


def meta(item_id):
    parsed = parse_id(item_id)
    if not parsed:
        return None
    if parsed[0] == "live":
        channel = _channel(parsed[1])
        return _channel_preview(channel) if channel else None
    from katan import cache

    _kind, module, mode, ref = parsed
    key = cache.make_key("stremio-meta", module, mode, ref)
    return cache.cached(key, lambda: _build_meta(module, mode, ref),
                        META_TTL) or None


def _build_meta(module, mode, ref):
    from katan.vod import extractors, library

    item = library.get(module, ref) or {}
    entries = extractors.episodes(module, ref, mode)
    if module == "keshet":
        entries = _open_mako_seasons(entries)
    videos = []
    for season, members, group in seasons(entries):
        for number, entry in zip(episode_numbers(members), members):
            videos.append(_video(module, mode, entry, season, number, group))
    if not videos and not item:
        return {}
    return {
        "id": vod_id(module, mode, ref),
        "type": "series",
        "name": display_name(item) or (entries[0].get("show_title")
                                       if entries else "") or "",
        "poster": _usable(item.get("art") or {}),
        "background": _usable(item.get("art") or {}),
        "description": item.get("plot") or "",
        "videos": videos,
    }


def _video(module, mode, entry, season, number, group):
    extra = entry.get("extra") or {}
    overview = entry.get("plot") or ""
    if group:
        overview = ("%s\n%s" % (group, overview)).strip()
    premiered = entry.get("premiered") or ""
    return {
        "id": vod_id(extra.get("module") or module, extra.get("mode") or mode,
                     extra.get("ref") or ""),
        "title": entry.get("title") or "",
        "season": season,
        "episode": number,
        "released": ("%sT00:00:00.000Z" % premiered[:10]
                     if re.match(r"^\d{4}-\d{2}-\d{2}", premiered) else UNDATED),
        "thumbnail": _usable(entry.get("art") or {}),
        "overview": overview,
    }


def _group_key(entry):
    season = entry.get("season")
    if isinstance(season, int) and season > 0:
        return str(season)
    return ((entry.get("extra") or {}).get("group") or "").strip()


def seasons(entries):
    """[(season number, entries, group name)] - Katan's grouping, for Stremio.

    Numbered seasons keep their numbers. A broadcaster that files by month
    (Now 14) gets its groups numbered in its own order, the month kept as
    the group name. Entries filed nowhere go to season 0, Stremio's
    "specials". Fewer than two groups is one flat season.
    """
    groups = collections.OrderedDict()
    loose = []
    for entry in entries:
        key = _group_key(entry)
        if key:
            groups.setdefault(key, []).append(entry)
        else:
            loose.append(entry)
    if len(groups) < 2:
        if len(groups) == 1:
            key = next(iter(groups))
            if key.isdigit():
                out = [(int(key), groups[key], "")]
                return out + ([(0, loose, "")] if loose else [])
        return [(1, list(entries), "")] if entries else []
    keys = list(groups)
    if all(key.isdigit() for key in keys):
        keys.sort(key=int)
        out = [(int(key), groups[key], "") for key in keys]
    else:
        out = [(n + 1, groups[key], key) for n, key in enumerate(keys)]
    if loose:
        out.append((0, loose, ""))
    return out


def episode_numbers(entries):
    """The broadcaster's numbers when they are all there and distinct,
    otherwise the order they came in."""
    numbers = [entry.get("episode") for entry in entries]
    if (all(isinstance(n, int) and n > 0 for n in numbers)
            and len(set(numbers)) == len(numbers)):
        return numbers
    return list(range(1, len(entries) + 1))


def _open_mako_seasons(entries):
    """Mako lists a season as a link to another page; open those pages."""
    from katan.vod.extractors import mako

    out = []
    opened = 0
    for entry in entries:
        ref = (entry.get("extra") or {}).get("ref") or ""
        if not (ref and mako._is_a_season(ref)) or opened >= MAX_SEASON_FOLDERS:
            out.append(entry)
            continue
        opened += 1
        found = re.search(r"(\d+)", entry.get("title") or "")
        number = int(found.group(1)) if found else opened
        for episode in mako.episodes(ref) or []:
            if mako._is_a_season((episode.get("extra") or {}).get("ref") or ""):
                continue
            episode = dict(episode)
            episode.setdefault("season", number)
            if not episode.get("season"):
                episode["season"] = number
            out.append(episode)
    return out


# --------------------------------------------------------------------------
# streams
# --------------------------------------------------------------------------


def streams(item_id):
    parsed = parse_id(item_id)
    if not parsed:
        return []
    kind, module, mode, ref = parsed
    if kind == "live":
        return _live_streams(module)
    from katan.vod import extractors, library

    url, _adaptive = extractors.stream(module, ref, mode)
    if not url:
        return []
    label = library.MODULE_NAMES.get(module, module)
    youtube = youtube_id(url)
    if youtube:
        return [{"ytId": youtube, "name": "Katan", "title": label}]
    return [_stream(url, label, "katan-" + module, PLAYER_HEADERS.get(module))]


def _live_streams(channel_id):
    from katan.vod import channels

    try:
        url, headers, _adaptive = channels.resolve(channel_id)
    except Exception:
        return []
    if not url:
        return []
    channel = _channel(channel_id) or {}
    return [_stream(url, channel.get("name") or "", "", headers or None)]


def _stream(url, title, binge_group, headers):
    hints = {}
    if not urlparse(url).path.lower().endswith(".mp4"):
        # HLS, DASH and MP3 go through Stremio's own streaming server.
        hints["notWebReady"] = True
    if binge_group:
        hints["bingeGroup"] = binge_group
    if headers:
        hints["notWebReady"] = True
        hints["proxyHeaders"] = {"request": dict(headers)}
    stream = {"url": url, "name": "Katan", "title": title}
    if hints:
        stream["behaviorHints"] = hints
    return stream


def youtube_id(url):
    """The video id when a broadcaster hands its episode to YouTube."""
    if url.startswith("plugin://plugin.video.youtube"):
        values = parse_qs(urlparse(url).query).get("video_id") or [""]
        return values[0]
    found = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{11})", url)
    return found.group(1) if found else ""


# --------------------------------------------------------------------------
# live channels
# --------------------------------------------------------------------------


def _live():
    from katan.vod import channels

    data = channels.load()
    rows = data.items() if isinstance(data, dict) else []
    live = []
    for channel_id, raw in rows:
        if not isinstance(raw, dict) or raw.get("type") != "tv":
            continue
        if raw.get("working") is False:
            continue
        live.append(dict(raw, id=channel_id))
    live.sort(key=lambda c: _int(c.get("index")))
    return live


def _channel(channel_id):
    for channel in _live():
        if channel["id"] == channel_id:
            return channel
    return None


def _channel_preview(channel):
    image = channel.get("image") or ""
    return {"id": live_id(channel["id"]), "type": "tv",
            "name": channel.get("name") or channel["id"],
            "poster": image if image.startswith("http") else None,
            "posterShape": "square"}


def _int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
