"""Turning normalised items into Kodi list items and directories.

Performance notes that matter on a weak box:

* Every ListItem is created with offscreen=True. Without it Kodi takes the GUI
  lock for each item, which is the single biggest cost of building a long list.
* Artwork is passed as URLs. Kodi downloads and caches textures itself, on its
  own threads, so the add-on never blocks on an image.
* Info is written through the Kodi 20+ InfoTagVideo setters when available and
  falls back to setInfo on older builds.
"""
import xbmcgui
import xbmcplugin

from .. import kodi, router, settings
from ..meta import items as meta_items

_HAS_INFOTAG = None


def _use_infotag():
    """Cache whether the modern InfoTagVideo setters exist."""
    global _HAS_INFOTAG
    if _HAS_INFOTAG is None:
        _HAS_INFOTAG = hasattr(xbmcgui.ListItem().getVideoInfoTag(), "setTitle")
    return _HAS_INFOTAG


MEDIA_TYPE = {
    "movie": "movie",
    "show": "tvshow",
    "season": "season",
    "episode": "episode",
    "channel": "video",
    "vod": "video",
}


def make_list_item(item, label=None):
    """Build a fully populated ListItem for one media item."""
    li = xbmcgui.ListItem(label=label or meta_items.label(item), offscreen=True)
    _set_art(li, item)
    if _use_infotag():
        _set_info_modern(li, item)
    else:
        _set_info_legacy(li, item)
    return li


def _set_art(li, item):
    art = dict(item.get("art") or {})
    poster = art.get("poster") or art.get("thumb") or ""
    thumb = art.get("thumb") or poster
    li.setArt({
        "poster": poster,
        "thumb": thumb,
        "icon": thumb,
        "fanart": art.get("fanart", ""),
        "clearlogo": art.get("clearlogo", ""),
        "banner": art.get("banner", ""),
    })


def _set_info_modern(li, item):
    tag = li.getVideoInfoTag()
    item_type = item.get("type", "movie")
    tag.setMediaType(MEDIA_TYPE.get(item_type, "video"))
    tag.setTitle(item.get("title") or "")
    if item.get("original_title"):
        tag.setOriginalTitle(item["original_title"])
    if item.get("plot"):
        tag.setPlot(item["plot"])
    if item.get("tagline"):
        tag.setTagLine(item["tagline"])
    if item.get("year"):
        tag.setYear(int(item["year"]))
    if item.get("premiered"):
        tag.setPremiered(item["premiered"])
    if item.get("rating"):
        tag.setRating(float(item["rating"]), int(item.get("votes") or 0))
    if item.get("genres"):
        tag.setGenres([g for g in item["genres"] if g])
    if item.get("duration"):
        tag.setDuration(int(item["duration"]))
    if item.get("mpaa"):
        tag.setMpaa(item["mpaa"])
    if item.get("studio"):
        tag.setStudios([s for s in item["studio"] if s])

    extra = item.get("extra") or {}
    if extra.get("director"):
        tag.setDirectors(extra["director"])
    if extra.get("writer"):
        tag.setWriters(extra["writer"])

    if item_type in ("episode", "season"):
        if item.get("show_title"):
            tag.setTvShowTitle(item["show_title"])
        if item.get("season") is not None:
            tag.setSeason(int(item.get("season") or 0))
        if item_type == "episode" and item.get("episode") is not None:
            tag.setEpisode(int(item.get("episode") or 0))

    unique = _unique_ids(item)
    if unique:
        tag.setUniqueIDs(unique, "tmdb" if "tmdb" in unique else list(unique)[0])

    tag.setPlaycount(int(item.get("playcount") or 0))

    resume = item.get("resume") or {}
    if resume.get("position"):
        tag.setResumePoint(float(resume["position"]), float(resume.get("total") or 0))

    cast = item.get("cast") or []
    if cast:
        try:
            import xbmc
            tag.setCast([
                xbmc.Actor(person.get("name", ""), person.get("role", ""),
                           order, person.get("thumb", ""))
                for order, person in enumerate(cast)
            ])
        except Exception:
            pass


def _set_info_legacy(li, item):  # pragma: no cover - only on Kodi 19
    info = {
        "mediatype": MEDIA_TYPE.get(item.get("type", "movie"), "video"),
        "title": item.get("title") or "",
        "originaltitle": item.get("original_title") or "",
        "plot": item.get("plot") or "",
        "year": int(item.get("year") or 0),
        "premiered": item.get("premiered") or "",
        "rating": float(item.get("rating") or 0),
        "votes": str(item.get("votes") or ""),
        "genre": item.get("genres") or [],
        "duration": int(item.get("duration") or 0),
        "mpaa": item.get("mpaa") or "",
        "studio": item.get("studio") or [],
        "playcount": int(item.get("playcount") or 0),
    }
    if item.get("type") in ("episode", "season"):
        info["tvshowtitle"] = item.get("show_title") or ""
        info["season"] = int(item.get("season") or 0)
        info["episode"] = int(item.get("episode") or 0)
    li.setInfo("video", info)
    unique = _unique_ids(item)
    if unique:
        li.setUniqueIDs(unique)


def _unique_ids(item):
    ids = item.get("ids") or {}
    out = {}
    for key in ("tmdb", "imdb", "tvdb", "trakt", "anilist"):
        if ids.get(key):
            out[key] = str(ids[key])
    return out


# --------------------------------------------------------------------------
# directories
# --------------------------------------------------------------------------

CONTENT_FOR_TYPE = {
    "movie": "movies",
    "show": "tvshows",
    "season": "seasons",
    "episode": "episodes",
}


def target_url(item):
    """Where selecting this item should go, and whether it is a folder."""
    item_type = item.get("type")
    ids = item.get("ids") or {}
    if item_type == "movie":
        return router.url_for("movie", tmdb=ids.get("tmdb"), imdb=ids.get("imdb")), False
    if item_type == "show":
        return router.url_for("seasons", tmdb=ids.get("tmdb")), True
    if item_type == "season":
        return router.url_for("episodes",
                              tmdb=(item.get("extra") or {}).get("tmdb_show") or ids.get("tmdb"),
                              season=item.get("season")), True
    if item_type == "episode":
        extra = item.get("extra") or {}
        return router.url_for("episode", tmdb=extra.get("tmdb_show"),
                              season=item.get("season"),
                              episode=item.get("episode")), False
    if item_type in ("channel", "vod"):
        return (item.get("extra") or {}).get("url", ""), False
    return "", False


def context_menu(item):
    """Right-click actions. Kept short; long menus are slow to open on a remote."""
    ids = item.get("ids") or {}
    entries = []
    item_type = item.get("type")

    if item_type in ("movie", "episode"):
        entries.append((kodi.localize(32250),
                        "RunPlugin(%s)" % router.url_for(
                            "sources", tmdb=ids.get("tmdb"), imdb=ids.get("imdb"),
                            type=item_type, season=item.get("season"),
                            episode=item.get("episode"))))
    if item_type in ("movie", "show"):
        entries.append((kodi.localize(32251),
                        "RunPlugin(%s)" % router.url_for(
                            "trakt_watchlist_add", tmdb=ids.get("tmdb"),
                            type=item_type)))
    if item_type in ("movie", "show", "episode"):
        entries.append((kodi.localize(32252),
                        "RunPlugin(%s)" % router.url_for(
                            "mark_watched", tmdb=ids.get("tmdb"), type=item_type,
                            season=item.get("season"), episode=item.get("episode"))))
    trailer = (item.get("extra") or {}).get("trailer")
    if trailer:
        entries.append((kodi.localize(32253), "PlayMedia(%s)" % trailer))
    return entries


def add_items(handle, item_list, content=None, sort_methods=None, cache_to_disc=True):
    """Render a list of media items as a Kodi directory in one batch call."""
    if not item_list:
        end(handle, content=content)
        return 0

    hide_watched = settings.get_bool("ui.hide_watched")
    batch = []
    for item in item_list:
        if hide_watched and item.get("playcount") and item.get("type") != "show":
            continue
        url, is_folder = target_url(item)
        if not url:
            continue
        li = make_list_item(item)
        if not is_folder:
            li.setProperty("IsPlayable", "true")
        menu = context_menu(item)
        if menu:
            li.addContextMenuItems(menu)
        batch.append((url, li, is_folder))

    if batch:
        xbmcplugin.addDirectoryItems(handle, batch, len(batch))
    content = content or CONTENT_FOR_TYPE.get(item_list[0].get("type"), "videos")
    end(handle, content=content, sort_methods=sort_methods, cache_to_disc=cache_to_disc)
    return len(batch)


def add_directory(handle, label, url, art=None, plot="", is_folder=True,
                  context=None, playable=False):
    """Add a single navigation entry such as a row heading or a menu item."""
    li = xbmcgui.ListItem(label=label, offscreen=True)
    art = art or {}
    li.setArt({
        "icon": art.get("icon", "DefaultFolder.png"),
        "thumb": art.get("thumb", art.get("icon", "")),
        "poster": art.get("poster", ""),
        "fanart": art.get("fanart", ""),
    })
    if _use_infotag():
        tag = li.getVideoInfoTag()
        tag.setMediaType("video")
        tag.setTitle(label)
        if plot:
            tag.setPlot(plot)
    else:  # pragma: no cover
        li.setInfo("video", {"title": label, "plot": plot})
    if playable:
        li.setProperty("IsPlayable", "true")
    if context:
        li.addContextMenuItems(context)
    xbmcplugin.addDirectoryItem(handle, url, li, is_folder)


def end(handle, content=None, sort_methods=None, cache_to_disc=True, succeeded=True):
    """Close a directory listing."""
    if content:
        xbmcplugin.setContent(handle, content)
    for method in sort_methods or ():
        xbmcplugin.addSortMethod(handle, method)
    xbmcplugin.endOfDirectory(handle, succeeded=succeeded,
                              updateListing=False, cacheToDisc=cache_to_disc)


def resolve(handle, url, item=None):
    """Hand a playable URL back to Kodi."""
    li = make_list_item(item) if item else xbmcgui.ListItem(offscreen=True)
    li.setPath(url)
    xbmcplugin.setResolvedUrl(handle, True, li)


def resolve_failed(handle):
    xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))


def resolve_stream(handle, url, adaptive=False, item=None, mime=""):
    """Hand a live or VOD stream to Kodi, wiring up adaptive playback.

    Headers are passed as a pipe-separated suffix on the URL, which is the
    convention Kodi understands for HLS and DASH sources that need a Referer.
    """
    li = make_list_item(item) if item else xbmcgui.ListItem(offscreen=True)
    li.setPath(url)

    if adaptive or ".mpd" in url.lower():
        manifest = "mpd" if ".mpd" in url.lower() else "hls"
        li.setProperty("inputstream", "inputstream.adaptive")
        # Kodi 21 dropped the inputstream.adaptive.manifest_type property in
        # favour of the mimetype, but setting both is harmless and keeps
        # Kodi 19 and 20 working.
        li.setProperty("inputstream.adaptive.manifest_type", manifest)
        li.setMimeType("application/dash+xml" if manifest == "mpd"
                       else "application/x-mpegURL")
        li.setContentLookup(False)
    elif mime:
        li.setMimeType(mime)
        li.setContentLookup(False)

    xbmcplugin.setResolvedUrl(handle, True, li)
