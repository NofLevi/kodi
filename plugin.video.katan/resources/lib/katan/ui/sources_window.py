"""The source picker.

Two lists behind one screen: the short ranked list the add-on recommends, and
everything that survived filtering. The short list is what opens, because the
whole point is to not make the viewer read forty rows of near-identical
releases before watching something.
"""
import xbmcgui

from .. import kodi, settings
from ..sources import model
from ..utils import release

ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92

LIST_SOURCES = 5200
BUTTON_TOGGLE = 9020
BUTTON_REFRESH = 9021


class SourcesWindow(xbmcgui.WindowXML):
    def __init__(self, *args, **kwargs):
        super(SourcesWindow, self).__init__()
        self.short = []
        self.full = []
        self.meta = {}
        self.showing_all = False
        self.chosen = None
        self.refresh_requested = False
        self.ready = False

    def onInit(self):
        if self.ready:
            return
        self.ready = True
        self.setProperty("katan.sources.title", _heading(self.meta))
        self._render()
        self.setFocusId(LIST_SOURCES)

    def onAction(self, action):
        if action.getId() in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self.close()

    def onClick(self, control_id):
        if control_id == LIST_SOURCES:
            self._choose()
        elif control_id == BUTTON_TOGGLE:
            self.showing_all = not self.showing_all
            self._render()
        elif control_id == BUTTON_REFRESH:
            self.refresh_requested = True
            self.close()

    # -- rendering ---------------------------------------------------------

    def _visible(self):
        return self.full if self.showing_all else self.short

    def _render(self):
        entries = self._visible()
        control = self.getControl(LIST_SOURCES)
        control.reset()
        for source in entries:
            control.addItem(_list_item(source))
        self.setProperty("katan.sources.status", _status(entries, self.showing_all))
        self.setProperty("katan.sources.toggle",
                         kodi.localize(32342 if self.showing_all else 32341))

    def _choose(self):
        entries = self._visible()
        try:
            position = self.getControl(LIST_SOURCES).getSelectedPosition()
        except Exception:
            return
        if 0 <= position < len(entries):
            self.chosen = entries[position]
            self.close()


def _list_item(source):
    label = source.get("title") or "?"
    li = xbmcgui.ListItem(label=label, label2=_detail(source), offscreen=True)
    li.setProperty("badge", _badge(source))
    return li


def _detail(source):
    """The grey line: where it came from and how big it is."""
    bits = []
    providers = source.get("providers") or [source.get("provider", "")]
    bits.append("/".join(p for p in providers if p))
    if source.get("size"):
        bits.append(release.size_label(source["size"]))
    if source.get("seeders"):
        bits.append(kodi.localize(32343, source["seeders"]))
    if source.get("audio") not in ("unknown", ""):
        bits.append(source["audio"].upper())
    return "  \u2022  ".join(b for b in bits if b)


def _badge(source):
    """The right-hand column: cached status and quality, the two that decide."""
    bits = []
    if source.get("cached"):
        service = source.get("cached_by") or ""
        bits.append("%s %s" % (kodi.localize(32330), service.upper()).strip())
    if "he" in (source.get("languages") or []):
        bits.append(kodi.localize(32344))
    bits.append((source.get("quality") or "sd").upper())
    if source.get("hdr"):
        bits.append("/".join(flag.upper() for flag in source["hdr"]))
    return "   ".join(bits)


def _heading(meta):
    title = meta.get("title") or ""
    if meta.get("type") == "episode":
        return "%s  %dx%02d" % (title, meta.get("season") or 0,
                                meta.get("episode") or 0)
    year = meta.get("year") or 0
    return "%s (%d)" % (title, year) if year else title


def _status(entries, showing_all):
    if not entries:
        return kodi.localize(32283)
    cached = sum(1 for s in entries if s.get("cached"))
    return "%s   %s" % (kodi.localize(32332, len(entries)),
                        kodi.localize(32345, cached))


def pick_source(sources, meta, all_sources=None):
    """Open the picker. Returns the chosen source, or None.

    A refresh request re-runs the search and reopens, which is why this loops
    rather than returning straight away.
    """
    from ..sources import aggregator

    short = list(sources)
    full = list(all_sources) if all_sources is not None else aggregator.all_sources(meta)

    while True:
        window = SourcesWindow("katan-sources.xml", kodi.addon_path(),
                               "default", "1080i")
        window.short = short
        window.full = full
        window.meta = meta
        try:
            window.doModal()
            chosen = window.chosen
            refresh = window.refresh_requested
        finally:
            del window

        if not refresh:
            return chosen
        aggregator.invalidate(meta)
        short = aggregator.find(meta, force=True)
        full = aggregator.all_sources(meta)
        if not short and not full:
            kodi.notify(kodi.localize(32283))
            return None


def quick_pick(sources, meta):
    """Fallback picker using the plain Kodi dialog, for tiny screens."""
    labels = ["%s  |  %s" % (model.label(s), s.get("title", "")[:60])
              for s in sources]
    index = kodi.select(labels, kodi.localize(32285))
    return sources[index] if index >= 0 else None
