"""The details screen.

Without this the add-on was click-and-it-plays: no plot, no cast, no episode
list, no way to pick a source before committing. That is the difference between
a scraper and something that feels like a streaming app.

For a movie it shows the information and the actions. For a show it also lists
the seasons, and then the episodes of the season chosen, in the same window
rather than pushing another screen onto the stack.
"""
import xbmcgui

from .. import kodi, router
from ..meta import items as meta_items, trakt_state
from . import listing

ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92

LIST_CONTENT = 5300
BUTTON_PLAY = 9100
BUTTON_SOURCES = 9101
BUTTON_TRAILER = 9102
BUTTON_WATCHLIST = 9103


class DetailsWindow(xbmcgui.WindowXML):
    def __init__(self, *args, **kwargs):
        super(DetailsWindow, self).__init__()
        self.item = {}
        self.entries = []          # seasons, then episodes once one is chosen
        self.season = None
        self.played = False
        self.ready = False

    def onInit(self):
        if self.ready:
            return
        self.ready = True
        self._paint()
        if self.item.get("type") == "show":
            self._load_seasons()
        self.setFocusId(BUTTON_PLAY)

    def onAction(self, action):
        if action.getId() not in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            return
        # Back steps out of the episode list before it leaves the window.
        if self.season is not None:
            self.season = None
            self._load_seasons()
            self.setFocusId(LIST_CONTENT)
            return
        self._cleanup()
        self.close()

    def onClick(self, control_id):
        if control_id == BUTTON_PLAY:
            self._play_best()
        elif control_id == BUTTON_SOURCES:
            self._play_best(force_picker=True)
        elif control_id == BUTTON_TRAILER:
            trailer = (self.item.get("extra") or {}).get("trailer")
            if trailer:
                kodi.play_media(trailer)
        elif control_id == BUTTON_WATCHLIST:
            self._add_to_watchlist()
        elif control_id == LIST_CONTENT:
            self._open_selected()

    # -- painting ----------------------------------------------------------

    def _paint(self):
        art = self.item.get("art") or {}
        self.setProperty("katan.detail.title", self.item.get("title") or "")
        self.setProperty("katan.detail.plot", self.item.get("plot") or "")
        self.setProperty("katan.detail.poster", art.get("poster", ""))
        self.setProperty("katan.detail.fanart",
                         art.get("fanart") or art.get("poster", ""))
        self.setProperty("katan.detail.meta", _meta_line(self.item))
        self.setProperty("katan.detail.cast", _cast_line(self.item))
        self.setProperty("katan.detail.trailer",
                         (self.item.get("extra") or {}).get("trailer", ""))
        self.setProperty("katan.detail.listheading", "")

    def _fill(self, entries, heading):
        self.entries = entries or []
        self.setProperty("katan.detail.listheading", heading if entries else "")
        try:
            control = self.getControl(LIST_CONTENT)
            control.reset()
            for entry in self.entries:
                li = listing.make_list_item(entry, label=_row_label(entry))
                li.setLabel2(_row_subtitle(entry))
                control.addItem(li)
        except Exception:
            kodi.log_exception("could not fill the details list")

    # -- shows -------------------------------------------------------------

    def _tmdb_id(self):
        return (self.item.get("ids") or {}).get("tmdb")

    def _load_seasons(self):
        from ..meta import tmdb

        tmdb_id = self._tmdb_id()
        if not tmdb_id:
            return
        seasons = trakt_state.annotate(tmdb.seasons(tmdb_id) or [])
        self._fill(seasons, kodi.localize(32391))

    def _load_episodes(self, season_number):
        from .. import settings
        from ..meta import tmdb
        from .handlers import _has_aired

        episodes = tmdb.episodes(self._tmdb_id(), season_number) or []
        if not settings.get_bool("ui.show_unaired"):
            episodes = [e for e in episodes if _has_aired(e)]
        self.season = season_number
        self._fill(trakt_state.annotate(episodes),
                   kodi.localize(32392, season_number))

    def _open_selected(self):
        try:
            position = self.getControl(LIST_CONTENT).getSelectedPosition()
        except Exception:
            return
        if not (0 <= position < len(self.entries)):
            return
        entry = self.entries[position]

        if entry.get("type") == "season":
            self._load_episodes(int(entry.get("season") or 0))
        elif entry.get("type") == "episode":
            self._play(entry)

    # -- playback ----------------------------------------------------------

    def _play_best(self, force_picker=False):
        if self.item.get("type") == "show":
            # Playing a show means playing the next unwatched episode.
            entry = self._next_unwatched()
            if entry is None:
                kodi.notify(kodi.localize(32393))
                return
            self._play(entry, force_picker)
            return
        self._play(self.item, force_picker)

    def _next_unwatched(self):
        for entry in self.entries:
            if entry.get("type") == "episode" and not entry.get("playcount"):
                return entry
        return None

    def _play(self, entry, force_picker=False):
        url, _is_folder = listing.target_url(entry)
        if not url:
            return

        if force_picker:
            ids = entry.get("ids") or {}
            extra = entry.get("extra") or {}
            url = router.url_for(
                "sources",
                tmdb=extra.get("tmdb_show") or ids.get("tmdb"),
                imdb=ids.get("imdb"), type=entry.get("type"),
                season=entry.get("season"), episode=entry.get("episode"))
            self._cleanup()
            self.close()
            kodi.run_builtin("RunPlugin(%s)" % url)
            return

        self.played = True
        self._cleanup()
        self.close()
        kodi.play_media(url)

    def _add_to_watchlist(self):
        ids = self.item.get("ids") or {}
        kodi.run_builtin("RunPlugin(%s)" % router.url_for(
            "trakt_watchlist_add", tmdb=ids.get("tmdb"),
            type=self.item.get("type", "movie")))

    def _cleanup(self):
        for name in ("title", "plot", "poster", "fanart", "meta", "cast",
                     "trailer", "listheading"):
            self.clearProperty("katan.detail.%s" % name)
        self.entries = []


def _meta_line(item):
    bits = []
    if item.get("year"):
        bits.append(str(item["year"]))
    if item.get("rating"):
        bits.append("%.1f" % item["rating"])
    if item.get("mpaa"):
        bits.append(item["mpaa"])
    if item.get("duration"):
        bits.append("%d min" % (item["duration"] // 60))
    genres = item.get("genres") or []
    if genres:
        bits.append(" / ".join(genres[:3]))
    return "  \u2022  ".join(bits)


def _cast_line(item):
    names = [person.get("name", "") for person in (item.get("cast") or [])[:6]]
    names = [name for name in names if name]
    if not names:
        return ""
    return "%s  %s" % (kodi.localize(32394), ", ".join(names))


def _row_label(entry):
    if entry.get("type") == "season":
        return entry.get("title") or kodi.localize(32392, entry.get("season") or 0)
    return meta_items.label(entry)


def _row_subtitle(entry):
    if entry.get("type") == "season":
        count = (entry.get("extra") or {}).get("episode_count") or 0
        return kodi.localize(32395, count) if count else ""
    bits = []
    if entry.get("premiered"):
        bits.append(entry["premiered"])
    if entry.get("duration"):
        bits.append("%d min" % (entry["duration"] // 60))
    return "  \u2022  ".join(bits)


def open_details(item):
    """Show the details for one item. Returns True if playback started."""
    if not item:
        return False

    full = _expand(item)
    window = DetailsWindow("katan-details.xml", kodi.addon_path(),
                           "default", "1080i")
    window.item = full
    try:
        window.doModal()
        played = window.played
    finally:
        del window
    return played


def _expand(item):
    """Rows hold summaries; the details screen needs cast, runtime and trailer."""
    from ..meta import tmdb

    tmdb_id = (item.get("ids") or {}).get("tmdb")
    if not tmdb_id or not tmdb.has_key():
        return item
    try:
        if item.get("type") == "movie":
            full = tmdb.movie(tmdb_id)
        elif item.get("type") == "show":
            full = tmdb.show(tmdb_id)
        else:
            return item
    except Exception:
        kodi.log_exception("could not expand the item for details")
        return item
    return full or item
