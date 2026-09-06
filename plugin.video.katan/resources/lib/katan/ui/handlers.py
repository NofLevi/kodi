"""Plugin route handlers.

Every route is small and delegates the real work. Kodi creates a fresh Python
interpreter for each plugin call, so the cheapest thing a handler can do is
import little and return quickly.
"""
import xbmcplugin

from .. import catalog, kodi, router, settings
from ..meta import items as meta_items
from . import listing


def _handle():
    return kodi.plugin_handle()


# --------------------------------------------------------------------------
# home
# --------------------------------------------------------------------------


@router.route("home")
def home(params):
    """The root listing.

    Opening the add-on should land in the Katan window, not in a Kodi file
    list, so that is the default and the plain listing is the fallback. The
    window is handed an empty directory so the plugin call finishes at once
    rather than holding the handle open for as long as the window lives.

    The one case where the window is not opened is having no TMDB key. Almost
    every row needs one, so the window would have nothing to draw; the plain
    listing at least explains itself and offers the wizard. The wizard then
    opens the window itself, so setting a key leads straight into the GUI
    rather than back to a file list.
    """
    from ..meta import tmdb

    handle = _handle()
    wants_window = (settings.get_bool("ui.window_home", True)
                    and params.get("nowindow") != "1")

    if wants_window and tmdb.has_key():
        listing.end(handle, succeeded=False)
        from .home_window import open_home
        open_home()
        return

    if not _require_tmdb(handle):
        return

    for row in catalog.enabled_rows():
        listing.add_directory(
            handle,
            catalog.row_title(row),
            router.url_for("row", id=row["id"]),
            art={"icon": "DefaultVideoPlaylists.png"},
        )

    # Live television and the on-demand catalogue are separate sections on
    # purpose: they are browsed in completely different ways.
    listing.add_directory(handle, kodi.localize(32217),
                          router.url_for("live_tv"),
                          art={"icon": "DefaultTVShows.png"})
    listing.add_directory(handle, kodi.localize(32218),
                          router.url_for("vod"),
                          art={"icon": "DefaultMovies.png"})
    # Only shown once a key is entered, because without one it would be a menu
    # entry that opens an empty screen.
    from ..meta import mdblist
    if mdblist.has_key():
        listing.add_directory(handle, kodi.localize(32232),
                              router.url_for("mdblist_lists"),
                              art={"icon": "DefaultVideoPlaylists.png"})

    listing.add_directory(handle, kodi.localize(32254),
                          router.url_for("search"),
                          art={"icon": "DefaultAddonsSearch.png"})
    listing.add_directory(handle, kodi.localize(32255),
                          router.url_for("tools"),
                          art={"icon": "DefaultAddonProgram.png"})
    listing.end(handle, content="videos")


@router.route("mdblist_lists")
def mdblist_lists(params):
    """The user's own MDBList lists, then the ones MDBList features."""
    from ..meta import mdblist

    handle = _handle()
    seen = set()
    for source in (mdblist.my_lists(), mdblist.top_lists()):
        for entry in source:
            if entry["id"] in seen:
                continue
            seen.add(entry["id"])
            label = entry["name"]
            if entry["count"]:
                label = "%s (%d)" % (label, entry["count"])
            listing.add_directory(
                handle, label,
                router.url_for("mdblist_list", id=entry["id"]),
                plot=entry["description"],
                art={"icon": "DefaultVideoPlaylists.png"})
    listing.end(handle, content="videos")


@router.route("mdblist_list")
def mdblist_list(params):
    """One MDBList list, resolved through TMDB so it has artwork."""
    from ..meta import mdblist

    handle = _handle()
    from .. import kids
    entries = kids.filter_items(mdblist.list_items(params.get("id", "")))
    listing.add_items(handle, entries, content="movies")


def _require_tmdb(handle):
    """Stop with a clear message rather than an empty screen when unconfigured."""
    from ..meta import tmdb
    if tmdb.has_key():
        return True
    listing.add_directory(handle, kodi.localize(32256),
                          router.url_for("setup"),
                          art={"icon": "DefaultAddonService.png"})
    listing.end(handle, content="videos", cache_to_disc=False)
    return False


@router.route("row")
def row(params):
    """One catalog row rendered as a normal Kodi list."""
    handle = _handle()
    row_id = params.get("id", "")
    entries = catalog.load(row_id)
    if not entries:
        kodi.notify(kodi.localize(32257))
    from ..meta import trakt_state
    trakt_state.annotate(entries)
    listing.add_items(handle, entries)


@router.route("seasons")
def seasons(params):
    from ..meta import tmdb, trakt_state
    handle = _handle()
    tmdb_id = params.get("tmdb")
    entries = tmdb.seasons(tmdb_id) if tmdb_id else []
    trakt_state.annotate(entries)
    listing.add_items(handle, entries, content="seasons")


@router.route("episodes")
def episodes(params):
    from ..meta import tmdb, trakt_state
    handle = _handle()
    tmdb_id = params.get("tmdb")
    season = int(params.get("season") or 0)
    entries = tmdb.episodes(tmdb_id, season) if tmdb_id else []
    if not settings.get_bool("ui.show_unaired"):
        entries = [e for e in entries if _has_aired(e)]
    trakt_state.annotate(entries)
    listing.add_items(handle, entries, content="episodes")


def _has_aired(episode):
    import time
    premiered = episode.get("premiered") or ""
    if not premiered:
        return True
    try:
        aired = time.strptime(premiered, "%Y-%m-%d")
    except ValueError:
        return True
    return time.mktime(aired) <= time.time()


# --------------------------------------------------------------------------
# playback
# --------------------------------------------------------------------------


@router.route("movie")
def play_movie(params):
    from .. import play
    play.play(_handle(), {
        "type": "movie",
        "tmdb": params.get("tmdb"),
        "imdb": params.get("imdb"),
    })


@router.route("episode")
def play_episode(params):
    from .. import play
    play.play(_handle(), {
        "type": "episode",
        "tmdb": params.get("tmdb"),
        "season": int(params.get("season") or 0),
        "episode": int(params.get("episode") or 0),
    })


@router.route("sources")
def show_sources(params):
    """Context-menu action: always open the picker, never autoplay."""
    from .. import play
    play.play(_handle(), {
        "type": params.get("type", "movie"),
        "tmdb": params.get("tmdb"),
        "imdb": params.get("imdb"),
        "season": int(params.get("season") or 0),
        "episode": int(params.get("episode") or 0),
    }, force_picker=True)


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------


@router.route("search")
def search(params):
    """Open the search window, or fall back to the modal keyboard."""
    handle = _handle()
    if settings.get_bool("ui.window_search", True):
        listing.end(handle, succeeded=False)
        from .search_window import open_search
        open_search()
        return
    query = kodi.keyboard(heading=kodi.localize(32254))
    if not query:
        listing.end(handle, succeeded=False)
        return
    _render_search(handle, query)


@router.route("search_query")
def search_query(params):
    _render_search(_handle(), params.get("q", ""))


def _render_search(handle, query):
    from ..search import unified
    from ..meta import trakt_state
    results = unified.search(query)
    if not results:
        kodi.notify(kodi.localize(32258))
    trakt_state.annotate(results)
    listing.add_items(handle, results, content="videos", cache_to_disc=False)


# --------------------------------------------------------------------------
# tools and setup
# --------------------------------------------------------------------------


@router.route("tools")
def tools(params):
    handle = _handle()
    entries = [
        (32260, router.url_for("setup")),
        (32396, router.url_for("accounts")),
        (32261, router.url_for("open_settings")),
        (32262, router.url_for("clear_cache")),
        (32263, router.url_for("cache_info")),
        (32264, router.url_for("rebuild_rows")),
        (32384, router.url_for("profile")),
        (32226, router.url_for("kids_toggle")),
        (32370, router.url_for("diagnostics")),
    ]
    for string_id, url in entries:
        listing.add_directory(handle, kodi.localize(string_id), url,
                              art={"icon": "DefaultAddonProgram.png"},
                              is_folder=False)
    listing.end(handle, content="files", cache_to_disc=False)


@router.route("setup")
def setup(params):
    from . import wizard
    wizard.run()


@router.route("open_settings")
def open_settings(params):
    settings.open_settings()


@router.route("clear_cache")
def clear_cache(params):
    from .. import cache
    if not kodi.yes_no(kodi.localize(32265)):
        return
    cache.clear()
    catalog.invalidate()

    # A debrid cache answer is remembered for an hour, including a negative
    # one, so a torrent that has since become cached still reads as uncached
    # until it expires. Clearing the cache is the one moment the user has
    # asked for exactly that to stop, and this was never wired up.
    try:
        from ..debrid import registry
        registry.forget_cache_status()
    except Exception:
        kodi.log_exception("clearing the debrid cache memory failed")

    kodi.notify(kodi.localize(32266))


@router.route("cache_info")
def cache_info(params):
    from .. import cache
    stats = cache.stats()
    kodi.ok_dialog(
        "%s: %d\n%s: %.1f MB\n%s: %d"
        % (kodi.localize(32267), stats["entries"],
           kodi.localize(32268), stats["file_bytes"] / (1024.0 * 1024.0),
           kodi.localize(32269), stats["expired"]))


@router.route("rebuild_rows")
def rebuild_rows(params):
    catalog.invalidate()
    count = catalog.warm(force=True)
    kodi.notify(kodi.localize(32270, count))


@router.route("kids_toggle")
def kids_toggle(params):
    """Turn kids mode on, or ask for the PIN to turn it off.

    The asymmetry is the point: switching it on is one confirmation, switching
    it off costs the PIN, so a child cannot undo it from the same menu.
    """
    from .. import kids

    if not kids.enabled():
        if not kids.has_pin():
            entered = kodi.keyboard("", kodi.localize(32228), hidden=True)
            if entered:
                kids.set_pin(entered)
        kids.turn_on()
        catalog.invalidate()
        kodi.notify(kodi.localize(32229))
        kodi.refresh_container()
        return

    entered = kodi.keyboard("", kodi.localize(32227), hidden=True) \
        if kids.has_pin() else ""
    if kids.turn_off(entered):
        catalog.invalidate()
        kodi.notify(kodi.localize(32230))
        kodi.refresh_container()
    else:
        kodi.notify(kodi.localize(32231))


@router.route("mark_watched")
def mark_watched(params):
    from ..meta import trakt_state
    trakt_state.mark_watched(params)
    kodi.refresh_container()


@router.route("trakt_watchlist_add")
def trakt_watchlist_add(params):
    from ..meta import trakt
    if trakt.add_to_watchlist(params.get("type", "movie"), params.get("tmdb")):
        kodi.notify(kodi.localize(32271))


# --------------------------------------------------------------------------
# Israeli live TV and VOD
# --------------------------------------------------------------------------


@router.route("live_tv")
def live_tv(params):
    """Live television, kept separate from the on-demand catalogue."""
    handle = _handle()
    from ..vod import channels

    entries = channels.live_channels(kind="tv")
    if settings.get_bool("vod.show_radio"):
        listing.add_directory(handle, kodi.localize(32360),
                              router.url_for("channels", kind="radio"),
                              art={"icon": "DefaultMusicVideos.png"})
    listing.add_items(handle, entries, content="videos", cache_to_disc=False)


@router.route("vod")
def vod(params):
    """The on-demand catalogue, browsed by broadcaster."""
    handle = _handle()
    from ..vod import library

    broadcasters = library.modules()
    if not broadcasters:
        kodi.notify(kodi.localize(32257))
        listing.end(handle, succeeded=False)
        return

    for module, count in broadcasters:
        listing.add_directory(
            handle,
            "%s (%d)" % (library.MODULE_NAMES.get(module, module), count),
            router.url_for("vod_module", module=module),
            art={"icon": "DefaultVideoPlaylists.png"})
    listing.end(handle, content="videos")


@router.route("channels")
def channels_list(params):
    from ..vod import channels
    handle = _handle()
    entries = channels.live_channels(kind=params.get("kind", "tv"))
    listing.add_items(handle, entries, content="videos", cache_to_disc=False)


@router.route("vod_module")
def vod_module(params):
    from ..vod import library
    handle = _handle()
    module = params.get("module", "")
    groups = library.categories(module)
    if len(groups) > 1:
        for name, count in groups:
            listing.add_directory(handle, "%s (%d)" % (name, count),
                                  router.url_for("vod_category", name=name),
                                  art={"icon": "DefaultVideoPlaylists.png"})
        listing.end(handle, content="videos")
        return
    listing.add_items(handle, library.by_module(module), content="videos")


@router.route("vod_category")
def vod_category(params):
    from ..vod import library
    listing.add_items(_handle(), library.by_category(params.get("name", "")),
                      content="videos")


@router.route("vod_show")
def vod_show(params):
    """Episodes of one programme, resolved by the per-broadcaster extractor."""
    from ..vod import extractors
    handle = _handle()
    entries = extractors.episodes(params.get("module", ""), params.get("ref", ""),
                                  params.get("mode", ""))
    if not entries:
        kodi.notify(kodi.localize(32361))
        listing.end(handle, succeeded=False)
        return
    listing.add_items(handle, entries, content="episodes", cache_to_disc=False)


@router.route("play_channel")
def play_channel(params):
    from ..vod import channels
    handle = _handle()
    url, adaptive = channels.play_url(params.get("id", ""))
    if not url:
        kodi.notify(kodi.localize(32362))
        listing.resolve_failed(handle)
        return
    listing.resolve_stream(handle, url, adaptive=adaptive)


@router.route("play_vod")
def play_vod(params):
    from ..vod import extractors
    handle = _handle()
    url, adaptive = extractors.stream(params.get("module", ""),
                                      params.get("ref", ""),
                                      params.get("mode", ""))
    if not url:
        kodi.notify(kodi.localize(32362))
        listing.resolve_failed(handle)
        return
    listing.resolve_stream(handle, url, adaptive=adaptive)


@router.route("diagnostics")
def diagnostics(params):
    """Run the device self-check and show it.

    This is the answer to "will it work on my box": it measures the things that
    differ between a PC and an Android stick, on the device itself.
    """
    from .. import diagnostics as checks

    kodi.busy_dialog(True)
    try:
        report = checks.run_and_log()
    finally:
        kodi.busy_dialog(False)

    import xbmcgui
    window = xbmcgui.Dialog()
    window.textviewer(kodi.localize(32370), report)


@router.route("profile")
def profile(params):
    """Choose a performance profile, with a recommendation for this device."""
    from .. import profiles

    suggested, why = profiles.recommend()
    order = ["low_memory", "balanced", "powerful"]
    labels = []
    for name in order:
        label = kodi.localize(profiles.NAMES[name])
        if name == suggested:
            label = "%s  <-- %s" % (label, kodi.localize(32383, why))
        if name == profiles.current():
            label = "* " + label
        labels.append("%s   (%s)" % (label, profiles.describe(name)))

    choice = kodi.select(labels, kodi.localize(32384),
                         preselect=order.index(profiles.current())
                         if profiles.current() in order else 1)
    if choice < 0:
        return
    changed = profiles.apply(order[choice])
    catalog.invalidate()
    kodi.notify(kodi.localize(32385, changed))


@router.route("accounts")
def accounts(params):
    """Show which services are connected, and let one be added or replaced.

    Debrid is the part most likely to be silently wrong: a token expires, a
    subscription lapses, and every source search quietly returns nothing. This
    screen makes that visible instead of leaving it to be inferred.
    """
    from ..debrid import registry
    from ..meta import tmdb, trakt

    handle = _handle()
    kodi.busy_dialog(True)
    try:
        rows = registry.account_summary()
    finally:
        kodi.busy_dialog(False)

    for row in rows:
        parts = [row["plan"] or ""]
        if row["user"]:
            parts.insert(0, row["user"])
        if row["expires"]:
            parts.append(kodi.localize(32397, str(row["expires"])[:10]))
        if row["is_free"]:
            parts.append(kodi.localize(32348))
        note = "  ".join(p for p in parts if p) or kodi.localize(32347)
        listing.add_directory(
            handle, "%s   %s" % (row["label"], note),
            router.url_for("connect", service=row["name"]),
            art={"icon": "DefaultAddonService.png"}, is_folder=False)

    for name, label, connected in (
            ("debrid", kodi.localize(32311), bool(rows)),
            ("trakt", "Trakt", trakt.authorised()),
            ("tmdb", "TMDB", tmdb.has_key())):
        if name == "debrid" and rows:
            continue
        mark = "[OK]" if connected else "[  ]"
        listing.add_directory(
            handle, "%s %s" % (mark, label),
            router.url_for("connect", service=name),
            art={"icon": "DefaultAddonService.png"}, is_folder=False)

    listing.end(handle, content="files", cache_to_disc=False)


@router.route("connect")
def connect(params):
    """Run the sign-in flow for one service."""
    from . import wizard

    service = params.get("service", "")
    if service == "trakt":
        wizard.step_trakt()
    elif service == "tmdb":
        wizard.step_tmdb()
    elif service == "debrid":
        wizard.step_debrid()
    else:
        from ..debrid import registry
        client = registry.get(service)
        if client is None:
            kodi.notify(kodi.localize(32320))
            return
        if client.authorize():
            kodi.notify(kodi.localize(32321, client.label))
        else:
            kodi.notify(kodi.localize(32322))
    kodi.refresh_container()
