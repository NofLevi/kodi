"""The source picker.

Two lists behind one screen: the short ranked list the add-on recommends, and
everything that survived filtering. The short list is what opens, because the
whole point is to not make the viewer read forty rows of near-identical
releases before watching something.
"""
import xbmcgui

from .. import kodi
from ..utils import release

ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92

LIST_SOURCES = 5200
BUTTON_REFRESH = 9021


class SourcesWindow(xbmcgui.WindowXML):
    def __init__(self, *args, **kwargs):
        super(SourcesWindow, self).__init__()
        self.short = []
        self.sources = []
        self.full = []
        self.closed = False
        self.meta = {}
        self.chosen = None
        self.refresh_requested = False
        self.ready = False
        self._drawn = []
        self.outlook = {}         # infohash -> what its subtitles look like

    def prepare(self):
        """Set what the window needs before it is shown.

        The title, status and toggle label are all window properties the skin
        reads, and the toggle button's whole label is one of them. Setting them
        inside onInit meant the first paint had a blank button, and the list
        could not take focus because Kodi had not yet decided it was visible.
        Home and search already do this; this window did not.
        """
        self.setProperty("pinky.sources.title", _heading(self.meta))
        self.setProperty("pinky.sources.status",
                         _status(self._visible(), self.meta))

    def onInit(self):
        if self.ready:
            return
        self.ready = True
        self.prepare()
        self._render()
        self.setFocusId(LIST_SOURCES)

    def close(self):
        self.closed = True
        super(SourcesWindow, self).close()

    def onAction(self, action):
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
        elif control_id == BUTTON_REFRESH:
            self.refresh_requested = True
            self.close()

    # -- rendering ---------------------------------------------------------

    def _visible(self):
        """What to draw, in order. `_first_page` has already built it.

        This briefly deduplicated by infohash, and that was wrong: the three
        subtitle lists are *supposed* to name the same release more than once.
        That is the comparison - NATIVE, LLM and ENGLISH are three answers to
        "how does this release get Hebrew", and a release with two of them
        earns two rows. Collapsing them left one row per release, so on
        Hikaru no Go every row read LLM while English subtitles fitting at
        100% sat in a list that had been thrown away.
        """
        return list(self.short)

    def _learnt(self):
        """The background pass found subtitles inside some releases: redraw.

        Naruto Shippuden 3x55 read "English subtitle, 75% fit" on every row
        while two of the files it listed carry an English track inside - in
        time by construction, 100% - because that is learnt in the background
        and was only drawn the next time the picker opened. The three lists
        are built from those fits, so the page is rebuilt, not relabelled,
        and the cursor stays on the release it was on.
        """
        if self.closed:
            return
        self.short = _first_page(self.meta, self.sources or self.short, self.full)
        self._render(learn=False)

    def _render(self, learn=True):
        """Fill the list.

        Wrapped because this runs inside onInit, where an exception is not a
        stack trace the viewer ever sees - it silently abandons the rest of
        onInit. That is exactly what used to happen: a crash building the badge
        for the first cached source left the picker with a title, no status, a
        blank toggle and an empty list, and nothing on screen said why.
        """
        entries = self._visible()
        # Which of these ship their own subtitles, before the rows are built,
        # because the mark is part of the row. One debrid lookup per cached
        # row under a four second ceiling, answers remembered per infohash,
        # and a row that does not answer is drawn exactly as it was before.
        try:
            from ..sources import bundled
            bundled.annotate(entries, self._learnt if learn else None)
        except Exception:
            kodi.log_exception("could not tell which releases carry subtitles")
        try:
            control = self.getControl(LIST_SOURCES)
            position = control.getSelectedPosition()
            control.reset()
            # One addItems, not addItem in a loop. Adding one at a time during
            # onInit only ever landed the first row on screen even though the
            # status line correctly counted eight; the home and search windows
            # both batch and both render fully.
            if not learn and 0 <= position < len(self._drawn):
                # A redraw reorders the lists; stay on the same release.
                same = _identity(self._drawn[position])
                position = next((i for i, row in enumerate(entries)
                                 if _identity(row) == same), position)
            control.addItems([_list_item(source, self.outlook)
                              for source in entries])
            self._drawn = list(entries)
            if position > 0:
                control.selectItem(position)
            kodi.log("sources picker: rendered %d of %d rows"
                     % (control.size(), len(entries)))
        except Exception:
            kodi.log_exception("could not render the source list")

        self.setProperty("pinky.sources.status", _status(entries, self.meta))

    def _choose(self):
        entries = self._visible()
        try:
            position = self.getControl(LIST_SOURCES).getSelectedPosition()
        except Exception:
            return
        if 0 <= position < len(entries):
            self.chosen = entries[position]
            self.close()


def _identity(source):
    """What makes two rows the same release: the infohash where there is one.

    Falling back to the name rather than comparing the objects, because the
    first page is built from `full` through the outlook split and a row there
    may be a copy of the one in `full` rather than the same dict.
    """
    return (source.get("hash") or "").lower() or (source.get("title") or "")


def _list_item(source, outlook=None):
    label = source.get("title") or "?"
    li = xbmcgui.ListItem(label=label, label2=_detail(source), offscreen=True)
    li.setProperty("badge", _badge(source))
    # Its own line rather than more text on the badge. Squeezed onto one line
    # the whole right-hand column was cut off - the screen read "...במטמון"
    # and the subtitle information, which is the reason any of it is there,
    # was the part that fell off the end.
    li.setProperty("subs", _subtitle_badge(source, outlook))
    return li


# Providers and debrid services are named by their module id internally. The
# viewer should read the name the service calls itself.
PROVIDER_NAMES = {
    "torrentio": "Torrentio",
    "torrentsdb": "TorrentsDB",
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


def _subtitle_badge(source, outlook=None):
    """What the words are likely to be, which for a Hebrew household is at
    least as decisive as the picture.

    Read straight off the source, because the aggregator has already worked
    it out - the same numbers that decided the order these rows are in. The
    picker used to fetch this itself on a worker thread and redraw; doing it
    once, upstream, means the badge and the ranking can never disagree.

    Two claims, deliberately worded differently. "Hebrew inside" is what the
    release name says, so it gets no percentage - it is somebody else's
    promise. A percentage is our own estimate of how well the best available
    subtitle matches *this* release, and it is the same number the subtitle
    chooser will show once the film is playing.
    """
    del outlook               # kept so older callers are not an error
    from ..subs import outlook as module

    mode = source.get("subs_mode")
    if mode in MODE_COLOURS:
        return _bundled_mark(source) + _mode_line(source, mode)

    kind = source.get("subs_kind")
    if not kind:
        return ""
    if kind == module.EMBEDDED:
        return kodi.localize(32474)
    if kind == module.EXTERNAL:
        return kodi.localize(32475, source.get("subs_score") or 0)
    if kind == module.AI:
        return kodi.localize(32535, source.get("subs_score") or 0)
    if kind == module.NATIVE:
        return kodi.localize(32537)
    return kodi.localize(32476)


# The two ways of getting Hebrew onto a release, told apart at a glance.
# Hebrew blue, AI yellow, English red. Chosen for contrast on the picker's own
# rows, not for looks: each is at least 4.5:1 on a highlighted row and 7:1 on
# a plain one, and the label carries a black shadow for a bright frame behind.
MODE_COLOURS = {"native": "FF6CB8FF", "llm": "FFFFD23F", "english": "FFFF7373"}
MODE_TAGS = {"native": 32539, "llm": 32540, "english": 32543}


def _bundled_mark(source):
    """A release that brings its own subtitles, in a language you read.

    Only in a language you read, which is the whole of the rule. A YTS
    release of Toy Story 5 ships French and Portuguese, and printing
    "FR/PT" beside a Hebrew percentage told a Hebrew household nothing,
    invited the two to be compared - they are about different subtitles -
    and was long enough to push the Hebrew figure off the end of the line,
    so the one number that decides anything read "Hebrew sub...".

    So the mark appears when the release carries Hebrew or English, and
    nothing otherwise. A subtitle nobody here can read is not a reason to
    take one release over another.
    """
    languages = list(source.get("bundled_subs") or [])
    languages += [code for code in source.get("inside_subs") or []
                  if code not in languages]
    if not languages:
        return ""
    try:
        from .. import settings
        readable = [code.lower() for code in settings.subtitle_languages() or []]
    except Exception:
        readable = ["he", "en"]
    useful = [code for code in languages if code in readable]
    if not useful:
        return ""
    return "[COLOR FF9BE38A][B]%s %s[/B][/COLOR]   " % (
        kodi.localize(32550), "/".join(code.upper() for code in useful[:2]))


def _mode_line(source, mode):
    """NATIVE or LLM, in its own colour, and how well the subtitle fits."""
    tag = "[COLOR %s][B]%s[/B][/COLOR]" % (
        MODE_COLOURS[mode], kodi.localize(MODE_TAGS[mode]))
    fit = int(source.get("subs_fit") or 0)
    if mode == "english":
        detail = kodi.localize(32544, fit)
    elif mode == "native":
        if "he" in (source.get("languages") or []) or fit >= 100 and \
                source.get("subs_kind") == "embedded":
            detail = kodi.localize(32474)
        else:
            detail = kodi.localize(32541, fit)
    else:
        detail = kodi.localize(32542, (source.get("subs_from") or "?").upper(),
                               fit)
    return "%s   [COLOR %s]%s[/COLOR]" % (tag, MODE_COLOURS[mode], detail)


def _badge(source, outlook=None):
    """The right-hand column: cached status and quality, the two that decide.

    `outlook` is accepted and ignored. The subtitle line is its own property
    now - see `_list_item` - and the argument stays so that a caller passing
    it is not an error while the tests and the window agree on one signature.
    """
    del outlook
    bits = []
    if source.get("cached"):
        # The brackets used to be missing here, so .strip() bound to the tuple
        # rather than to the formatted string. Every cached source raised, and
        # because cached sources rank first, the very first row killed the
        # whole list. The picker was empty for anyone who opened it.
        bits.append(("%s %s" % (kodi.localize(32330),
                                _service_label(source.get("cached_by")))).strip())
    else:
        # Said, not implied. An uncached source is perfectly playable - the
        # debrid service fetches it and it is there a moment later - so the
        # row belongs on the page. What it must not do is look identical to
        # one that starts instantly.
        bits.append(kodi.localize(32556))
    if "he" in (source.get("languages") or []):
        bits.append(kodi.localize(32344))

    # An unknown quality is unknown. Labelling it SD is a claim, not a default.
    quality = source.get("quality") or ""
    if quality and quality != "unknown":
        bits.append(quality.upper())

    if source.get("hdr"):
        bits.append("/".join(flag.upper() for flag in source["hdr"]))

    # DUB, SUB or DUAL, which anime publishes as two separate releases of the
    # same episode and the release name is the only place that says which.
    # `release.parse` has read this into a `dub` field all along and
    # `model.label` has rendered it all along - and `model.label` has no
    # callers, so this has never once been on a screen. Two rows of the same
    # resolution, size and group are indistinguishable without it, so choosing
    # the dub meant starting one and backing out.
    #
    # Last, because it is the rarest: it is blank for everything that is not
    # anime, deliberately, since "MULTI.SUBS" on a live-action film is a claim
    # about its subtitles and says nothing about the audio.
    if source.get("dub"):
        bits.append(source["dub"].upper())
    return "   ".join(bits)


def _heading(meta):
    title = meta.get("title") or ""
    if meta.get("type") == "episode":
        return "%s  %dx%02d" % (title, meta.get("season") or 0,
                                meta.get("episode") or 0)
    year = meta.get("year") or 0
    return "%s (%d)" % (title, year) if year else title


def _status(entries, meta=None):
    """The line under the title: how many, how many cached, and what was cut.

    The last part is the one that was missing. "8 sources" for a film with
    sixty-four releases reads as a broken picker; "8 of 64, 40 camera
    recordings hidden" reads as the filters doing their job, which is what
    was happening. Everything that passed the filters is now on screen, so what
    is missing really is the filters' doing and the note is always true.
    """
    if not entries:
        return kodi.localize(32283)
    # Rows are not sources. The three subtitle lists name one release once per
    # route it has, so The Odyssey drew 34 rows from 22 releases and the line
    # read "34 sources ... of 38, 4 hidden (15 camera recordings, 1 above the
    # resolution limit)" - 15 and 1 do not make 4, because 34 was a row count
    # being subtracted from a source count.
    unique = []
    seen = set()
    for source in entries:
        key = _identity(source)
        if key in seen:
            continue
        seen.add(key)
        unique.append(source)
    entries = unique
    cached = sum(1 for s in entries if s.get("cached"))
    parts = [kodi.localize(32332, len(entries)),
             kodi.localize(32345, cached)]
    if meta:
        note = _why_hidden(entries, meta)
        if note:
            parts.append(note)
        hebrew = _hebrew_note(entries, meta)
        if hebrew:
            parts.append(hebrew)
    return "   ".join(parts)


# `scoring.rejection_reason` answers in English, which is right for the log
# and wrong on the screen: the status line read "118 מוסתרים (32 below the
# resolution limit)", half a sentence in each language. The reasons stay
# English where they are produced - they are compared in tests and read in
# logs - and are translated here, where they are shown.
REASON_STRINGS = {
    "cam release": 32480,
    "HEVC is switched off": 32481,
    "AV1 is switched off": 32482,
    "HDR is switched off": 32483,
    "Dolby Vision without an HDR10 fallback": 32527,
    "another production of the same name": 32534,
    "above the resolution limit": 32484,
    "below the resolution limit": 32485,
    "larger than the size limit": 32486,
    "far too small for its claimed quality": 32487,
    "implausibly large": 32488,
    "not cached": 32489,
    "another series of the same name": 32557,
    "no seeders": 32558,
}


def _reason_label(reason):
    string_id = REASON_STRINGS.get(reason)
    if not string_id:
        return reason           # a reason nobody has translated yet
    text = kodi.localize(string_id)
    return text if text and text != str(string_id) else reason


def _hebrew_note(entries, meta):
    """Say why there is no Hebrew block, because absent looks like broken.

    An empty NATIVE list reads exactly like a failed search, and the two are
    different things. Measured on The Odyssey (2026): OpenSubtitles holds 74
    subtitles for it - Arabic 7, Greek 7, Albanian 6 - and **none in Hebrew**;
    Ktuvit does not have the film; Wizdom answers nothing for its id. There
    was no Hebrew subtitle to find, and nothing on the screen said so.

    The other half matters too. When Hebrew exists but fits none of these
    releases, that is a different answer and points at a different remedy -
    choose another release, or translate.
    """
    if any(row.get("subs_mode") == "native" for row in entries):
        return ""
    try:
        from ..subs import outlook, auto
        if auto.normalise_language(meta.get("original_language")) == "he":
            return ""
        found = outlook.candidates(meta)
        if found:
            # Only the ones that are for *this* title. `candidates` returns
            # whatever the providers answered, and on The Odyssey all twelve
            # were for other shows - Doctor Odyssey, The Odyssey 1997, The
            # Simpsons - every one of them scored zero as the wrong title. The
            # line then read "Hebrew subtitles exist but none fits these
            # releases", which is a statement about the wrong twelve files.
            from ..subs import matcher
            target = matcher.target_from(meta)
            found = [c for c in found if matcher.rate(c, target)[0] > 0]
    except Exception:
        return ""
    if found is None:
        return ""
    if found:
        return kodi.localize(32554)
    # No Hebrew anywhere is not a dead end while there is something to
    # translate. Measured over 65 titles: counting the best route rather than
    # the Hebrew one, anime goes from an average of 31 to **98**, and ten of
    # its fifteen have no Hebrew subtitle in existence. Saying only "none
    # exists" describes the corpus and not what the viewer is about to get.
    if any(row.get("subs_mode") == "llm" for row in entries):
        return kodi.localize(32555)
    return kodi.localize(32553)


def _why_hidden(entries, meta):
    """"of 64, 40 cam releases hidden", or "" when there is nothing to say."""
    from ..sources import aggregator

    report = aggregator.filter_report(meta)
    if not report:
        return ""
    found = int(report.get("found") or 0)
    reasons = report.get("reasons") or []
    # Only what was actually rejected. "Found minus drawn" also counted the
    # same file reported twice and releases not drawn yet, and printed
    # "of 10, 9 hidden ()" on Hikaru no Go - nine hidden for no reason at all.
    hidden = sum(int(count or 0) for _reason, count in reasons)
    if hidden <= 0:
        return ""
    biggest = ", ".join("%d %s" % (count, _reason_label(reason))
                        for reason, count in reasons[:2])
    return kodi.localize(32470, found, hidden, biggest)


def _plain(short, full):
    """The ordinary list, for a title with no subtitle comparison to make.

    A Hebrew title, or a search where no release has a subtitle of either
    kind. Here a release means one row, so this is the one place the rows are
    deduplicated - by infohash, because two providers reporting one torrent
    agree on that and can differ on the name.
    """
    seen = set()
    ordered = []
    for source in list(short or []) + list(full or []):
        key = _identity(source)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(source)
    return ordered


def _first_page(meta, short, full):
    """Ten NATIVE, ten LLM, five ENGLISH. Twenty-five rows, and no more.

    Asked for in so many words: the first rows should put the two ways of
    getting Hebrew side by side, so which one works is something the viewer
    sees rather than something decided for them.

    Asked for in so many words, and the cap is part of it. Everything that
    passed the filters was shown for a while, tagged, and it was worse: 118
    rows on Toy Story 5, most of them a release nobody would choose, with the
    three lists that are the whole point of the page buried inside them. The
    page is a comparison between three ways of getting Hebrew, and twenty-five
    rows is what a comparison is.

    The ordinary short list comes back whenever there is nothing to split - a
    Hebrew title, or a search where no release has a subtitle of either kind.
    """
    try:
        from ..subs import outlook
        native, llm, english = outlook.split_rows(meta, full or short)
    except Exception:
        kodi.log_exception("could not split the sources into native and AI")
        return _plain(short, full)
    if not native and not llm and not english:
        return _plain(short, full)
    # A release with no subtitle evidence *yet* is still a release. They used
    # to vanish from this page: Hikaru no Go 3x02 kept six releases and drew
    # one, because only one had a subtitle anybody had indexed - while the
    # other five were a torrent shipping 76 English .srt files and MKVs with
    # English inside, which nothing had looked at yet. They go after the
    # three lists, as plain rows.
    listed = set(_identity(row) for row in native + llm + english)
    rest = [source for source in _plain(short, [])
            if _identity(source) not in listed]
    kodi.log("sources picker: %d native rows, %d AI rows, %d English rows, "
             "%d with no subtitle known yet"
             % (len(native), len(llm), len(english), len(rest)))
    return _in_one_order(native + llm + english) + rest


# Hebrew first where two rows fit equally well, because a subtitle somebody
# made beats one a model makes, and English last for the same reason reversed.
_ROUTE_ORDER = {"native": 0, "llm": 1, "english": 2}


def _in_one_order(rows):
    """Hebrew first, then AI, then English - always, and by fit inside each.

    Sorting the whole page by fit was tried and is wrong. On The Odyssey it
    put three LLM rows at 76% above seven NATIVE rows at 70%, which reads as
    "translate this" on a film that has a Hebrew subtitle somebody made. The
    three lists are a ladder, not a scoreboard: a Hebrew subtitle beats a
    translation of one, and a translation beats reading English, whatever the
    fits say. The fit orders the rungs, it does not reorder the ladder.
    """
    return sorted(rows, key=lambda row: (_ROUTE_ORDER.get(row.get("subs_mode"), 3),
                                         -(row.get("subs_fit") or 0)))


def pick_source(sources, meta, all_sources=None):
    """Open the picker. Returns the chosen source, or None.

    A refresh request re-runs the search and reopens, which is why this loops
    rather than returning straight away.
    """
    from ..sources import aggregator

    full = list(all_sources) if all_sources is not None else aggregator.all_sources(meta)
    short = _first_page(meta, sources, full)

    while True:
        window = SourcesWindow("pinky-sources.xml", kodi.addon_path(),
                               "default", "1080i")
        window.short = short
        window.sources = sources
        window.full = full
        window.meta = meta
        try:
            # Before showing, so the first paint has a title, a status line and
            # a toggle button with a label on it.
            window.prepare()
            kodi.clear_busy_dialogs()
            window.doModal()
            chosen = window.chosen
            refresh = window.refresh_requested
        finally:
            del window

        if not refresh:
            return chosen
        aggregator.invalidate(meta)
        found = aggregator.find(meta, force=True)
        full = aggregator.all_sources(meta)
        short = _first_page(meta, found, full)
        if not short and not full:
            kodi.notify(kodi.localize(32283))
            return None
