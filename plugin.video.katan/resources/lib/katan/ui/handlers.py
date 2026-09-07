"""Plugin route handlers.

Every route is small and delegates the real work. Kodi creates a fresh Python
interpreter for each plugin call, so the cheapest thing a handler can do is
import little and return quickly.
"""

from .. import catalog, kodi, router, settings
from . import listing


def _handle():
    return kodi.plugin_handle()


# Rows that the plain listing offers as sections instead, because they are the
# same two things under the same headings and listing both showed each twice.
SECTION_ROWS = ("israel_live", "israel_vod")


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
        # The Israeli rows are the same two things the sections below are, with
        # the same headings - 32217 and 32218 - so listing both put "שידורים
        # חיים" and "VOD ישראלי" on this screen twice each, one above the
        # other. In the custom window they are horizontal rows of artwork and
        # the sections are not there at all, so the duplication is only here.
        # The folder is the better of the two in a plain list.
        if row["id"] in SECTION_ROWS:
            continue
        # A row that has been warmed and came back empty is not offered. The
        # custom window already hides those; the plain listing was still
        # showing them, so a Trakt chart with no account signed in was a menu
        # entry that opens an empty screen. peek() returning None means "not
        # warmed yet", which is not the same thing and must still be offered.
        warmed = catalog.peek(row["id"])
        if warmed is not None and not warmed:
            continue
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
    """Offer setup rather than an empty screen, but not *only* setup.

    Everything TMDB feeds needs a key. Israeli live television and the
    on-demand catalogue need nothing at all - they are bundled data and the
    broadcasters' own streams - and hiding them behind a gate for a key they
    do not use meant a fresh install showed exactly one line, "set up Katan",
    with forty-three working channels behind it.

    So the gate still leads with setup, and then offers the half that already
    works.
    """
    from ..meta import tmdb
    if tmdb.has_key():
        return True
    listing.add_directory(handle, kodi.localize(32256),
                          router.url_for("setup"),
                          art={"icon": "DefaultAddonService.png"})
    listing.add_directory(handle, kodi.localize(32217),
                          router.url_for("live_tv"),
                          art={"icon": "DefaultTVShows.png"})
    listing.add_directory(handle, kodi.localize(32218),
                          router.url_for("vod"),
                          art={"icon": "DefaultMovies.png"})
    listing.add_directory(handle, kodi.localize(32255),
                          router.url_for("tools"),
                          art={"icon": "DefaultAddonProgram.png"})
    listing.end(handle, content="videos", cache_to_disc=False)
    return False


@router.route("row")
def row(params):
    """One catalog row rendered as a normal Kodi list, a page at a time.

    A directory in Kodi is a fixed list - there is no scroll event to hang a
    fetch off, which is why the custom window grows its rows and this cannot.
    The idiomatic answer, and the one Kodi's own skins already understand, is
    a "next page" entry at the end, so the listing stays one page long however
    deep the viewer goes.
    """
    handle = _handle()
    row_id = params.get("id", "")
    page = _page(params)
    entries = catalog.load(row_id, page=page)
    if not entries:
        kodi.notify(kodi.localize(32257))
    from ..meta import trakt_state
    trakt_state.annotate(entries)
    listing.add_items(handle, entries)
    if entries and catalog.has_more(row_id):
        listing.add_directory(
            handle,
            kodi.localize(32416),
            router.url_for("row", id=row_id, page=page + 1),
            art={"icon": "DefaultFolderBack.png"},
        )


def _page(params):
    """The page number from a url, which a viewer can also type wrongly."""
    try:
        return max(1, int(params.get("page", 1)))
    except (TypeError, ValueError):
        return 1


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
                                  router.url_for("vod_category", name=name,
                                                 module=module),
                                  art={"icon": "DefaultVideoPlaylists.png"})
        listing.end(handle, content="videos")
        return
    listing.add_items(handle, library.by_module(module), content="videos")


@router.route("vod_category")
def vod_category(params):
    # The broadcaster travels with the category. The counts beside these names
    # were worked out within one broadcaster, so opening one without it showed
    # a different, longer list than the number promised.
    from ..vod import library
    listing.add_items(_handle(),
                      library.by_category(params.get("name", ""),
                                          module=params.get("module", "")),
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


def _mark(connected):
    """The signed-in marker. Deliberately not an emoji: the Kodi list font
    renders one as a box on some Android builds, and a box beside every
    account is worse than no marker at all."""
    return "[OK]" if connected else "[  ]"


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
        # The same marker the other rows use. A debrid row carried a note and
        # no marker, so a service whose token had expired looked much like one
        # that was fine - on the screen whose whole purpose is to say which is
        # which.
        listing.add_directory(
            handle, "%s %s   %s" % (_mark(row["connected"]), row["label"], note),
            router.url_for("connect", service=row["name"]),
            art={"icon": "DefaultAddonService.png"}, is_folder=False)

    # Always, even when one is already connected. This entry used to be
    # skipped as soon as any debrid service was signed in, which meant the
    # only way to add a second one was to have had none - so a viewer with
    # TorBox could not reach Real-Debrid from this screen at all, and the
    # screen gave no hint that the other three existed.
    listing.add_directory(
        handle, "%s %s" % (_mark(bool(rows)), kodi.localize(32311)),
        router.url_for("connect", service="debrid"),
        art={"icon": "DefaultAddonService.png"}, is_folder=False)

    for name, label, connected in (
            ("trakt", "Trakt", trakt.authorised()),
            ("tmdb", "TMDB", tmdb.has_key())):
        listing.add_directory(
            handle, "%s %s" % (_mark(connected), label),
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
    elif service == "opensubtitles":
        wizard.step_opensubtitles()
    else:
        from ..debrid import registry
        client = registry.get(service)
        if client is None:
            kodi.notify(kodi.localize(32320))
            return
        if not _connect_or_disconnect(client):
            return
    kodi.refresh_container()


def _connect_or_disconnect(client):
    """Sign in, or sign out of an account that is already connected.

    A connected service used to offer only "connect again", which is the one
    thing somebody looking at a working account does not want. Signing out
    matters more than it sounds: a stale token makes every source search
    quietly return nothing, and clearing it is the fix.
    """
    from . import wizard

    if client.configured():
        options = [kodi.localize(32468),
                   kodi.localize(32469) % client.label]
        choice = kodi.select(options, client.label)
        if choice < 0:
            return False
        if choice == 1:
            client.sign_out()
            kodi.notify(kodi.localize(32467))
            return True

    if wizard.connect(client, client.label):
        kodi.notify(kodi.localize(32321, client.label))
    else:
        kodi.notify(kodi.localize(32322))
    return True
