"""The source picker.

Two lists behind one screen: the short ranked list the add-on recommends, and
everything that survived filtering. The short list is what opens, because the
whole point is to not make the viewer read forty rows of near-identical
releases before watching something.
"""
import xbmcgui

from .. import kodi, settings
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
        self.outlook = {}         # infohash -> what its subtitles look like

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
                         _status(self._visible(), self.showing_all, self.meta))
        self.setProperty("katan.sources.toggle",
                         kodi.localize(32342 if self.showing_all else 32341))

    def onInit(self):
        if self.ready:
            return
        self.ready = True
        self.prepare()
        self._render()
        self.setFocusId(LIST_SOURCES)
        self._look_up_subtitles()

    def _look_up_subtitles(self):
        """Find out what each source's subtitles look like, on a worker.

        On the GUI thread this would hold the picker closed for as long as
        the subtitle providers take, which is the wrong trade: the list is
        useful immediately and the subtitle column is an extra. It arrives a
        moment later and the rows are drawn again, which is safe here in a
        way it was not for the home rows - this list is rebuilt wholesale
        rather than appended to, and it has no cursor worth keeping because
        nobody has had time to move it yet.
        """
        import threading

        def worker():
            try:
                from ..subs import outlook
                found = outlook.for_sources(self.meta, self.full or self.short)
            except Exception:
                kodi.log_exception("subtitle outlook failed")
                return
            if not found:
                return
            self.outlook = found
            self.pending_redraw = True

        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()

    def onAction(self, action):
        if getattr(self, "pending_redraw", False):
            # Same rule as the home rows: a list is only ever changed on the
            # GUI thread, which is here.
            self.pending_redraw = False
            self._render()
        if action.getId() in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self.close()

    def onClick(self, control_id):
        # Logged because the window has three ways out and, from a screenshot
        # alone, a picker that closed tells you nothing about which one was
        # taken. This is the difference between "the toggle is broken" and
        # "you pressed the other button".
        kodi.log("sources picker: control %s clicked" % control_id)
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
            position = control.getSelectedPosition()
            control.addItems([_list_item(source, self.outlook)
                              for source in entries])
            if position > 0:
                control.selectItem(position)
            kodi.log("sources picker: rendered %d of %d rows"
                     % (control.size(), len(entries)))
        except Exception:
            kodi.log_exception("could not render the source list")

        self.setProperty("katan.sources.status", _status(entries, self.showing_all, self.meta))
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


def _list_item(source, outlook=None):
    label = source.get("title") or "?"
    li = xbmcgui.ListItem(label=label, label2=_detail(source), offscreen=True)
    li.setProperty("badge", _badge(source, outlook))
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


def _subtitle_badge(source, outlook):
    """What the words are likely to be, which for a Hebrew household is at
    least as decisive as the picture.

    Two different claims, deliberately worded differently. "Hebrew inside"
    is what the release name says, so it gets no percentage - it is somebody
    else's promise. A percentage is our own estimate of how well the best
    available subtitle matches *this* release, and it is the same number the
    subtitle chooser will show once the film is playing.
    """
    if not outlook:
        return ""
    from ..subs import outlook as module

    entry = outlook.get(source.get("hash") or source.get("title") or "")
    if not entry:
        return ""
    if entry.get("kind") == module.EMBEDDED:
        return kodi.localize(32474)
    if entry.get("kind") == module.EXTERNAL:
        return kodi.localize(32475, entry.get("score") or 0)
    return kodi.localize(32476)


def _badge(source, outlook=None):
    """The right-hand column: cached status and quality, the two that decide."""
    bits = []
    subtitles = _subtitle_badge(source, outlook)
    if subtitles:
        bits.append(subtitles)
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


def _status(entries, showing_all, meta=None):
    """The line under the title: how many, how many cached, and what was cut.

    The last part is the one that was missing. "8 sources" for a film with
    sixty-four releases reads as a broken picker; "8 of 64, 40 camera
    recordings hidden" reads as the filters doing their job, which is what
    was happening. The reason is only shown while everything is on screen -
    with the short list up, the number that is missing is mostly the short
    list's own doing and saying otherwise would be misleading.
    """
    if not entries:
        return kodi.localize(32283)
    cached = sum(1 for s in entries if s.get("cached"))
    parts = [kodi.localize(32332, len(entries)),
             kodi.localize(32345, cached)]
    if showing_all and meta:
        note = _why_hidden(entries, meta)
        if note:
            parts.append(note)
    return "   ".join(parts)


def _why_hidden(entries, meta):
    """"of 64, 40 cam releases hidden", or "" when there is nothing to say."""
    from ..sources import aggregator

    report = aggregator.filter_report(meta)
    if not report:
        return ""
    found = int(report.get("found") or 0)
    hidden = found - len(entries)
    if hidden <= 0:
        return ""
    reasons = report.get("reasons") or []
    biggest = ", ".join("%d %s" % (count, reason)
                        for reason, count in reasons[:2])
    return kodi.localize(32470, found, hidden, biggest)


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
