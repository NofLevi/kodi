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

    def prepare(self):
        """Set what the window needs before it is shown.

        The title, status and toggle label are all window properties the skin
        reads, and the toggle button's whole label is one of them. Setting them
        inside onInit meant the first paint had a blank button, and the list
        could not take focus because Kodi had not yet decided it was visible.
        Home and search already do this; this window did not.
        """
        self.setProperty("katan.sources.title", _heading(self.meta))
        self.setProperty("katan.sources.status",
                         _status(self._visible(), self.showing_all))
        self.setProperty("katan.sources.toggle",
                         kodi.localize(32342 if self.showing_all else 32341))

    def onInit(self):
        if self.ready:
            return
        self.ready = True
        self.prepare()
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
        """Fill the list.

        Wrapped because this runs inside onInit, where an exception is not a
        stack trace the viewer ever sees - it silently abandons the rest of
        onInit. That is exactly what used to happen: a crash building the badge
        for the first cached source left the picker with a title, no status, a
        blank toggle and an empty list, and nothing on screen said why.
        """
        entries = self._visible()
        try:
            control = self.getControl(LIST_SOURCES)
            control.reset()
            # One addItems, not addItem in a loop. Adding one at a time during
            # onInit only ever landed the first row on screen even though the
            # status line correctly counted eight; the home and search windows
            # both batch and both render fully.
            control.addItems([_list_item(source) for source in entries])
            kodi.log("sources picker: rendered %d of %d rows"
                     % (control.size(), len(entries)))
        except Exception:
            kodi.log_exception("could not render the source list")

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


# Providers and debrid services are named by their module id internally. The
# viewer should read the name the service calls itself.
PROVIDER_NAMES = {
    "torrentio": "Torrentio",
    "comet": "Comet",
    "mediafusion": "MediaFusion",
    "zilean": "Zilean",
    "nyaa": "Nyaa",
    "animetosho": "AnimeTosho",
    "external": "CocoScrapers",
}


def _provider_label(name):
    return PROVIDER_NAMES.get(name, name.title() if name else "")


def _service_label(name):
    """The debrid service's own name: "TorBox", not "TORBOX"."""
    if not name:
        return ""
    try:
        from ..debrid import registry
        service = registry.get(name)
        if service is not None and getattr(service, "label", ""):
            return service.label
    except Exception:
        pass
    return name.title()


def _detail(source):
    """The grey line: where it came from and how big it is."""
    bits = []
    providers = source.get("providers") or [source.get("provider", "")]
    bits.append(" / ".join(_provider_label(p) for p in providers if p))
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
        # The brackets used to be missing here, so .strip() bound to the tuple
        # rather than to the formatted string. Every cached source raised, and
        # because cached sources rank first, the very first row killed the
        # whole list. The picker was empty for anyone who opened it.
        bits.append(("%s %s" % (kodi.localize(32330),
                                _service_label(source.get("cached_by")))).strip())
    if "he" in (source.get("languages") or []):
        bits.append(kodi.localize(32344))

    # An unknown quality is unknown. Labelling it SD is a claim, not a default.
    quality = source.get("quality") or ""
    if quality and quality != "unknown":
        bits.append(quality.upper())

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
            # Before showing, so the first paint has a title, a status line and
            # a toggle button with a label on it.
            window.prepare()
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
