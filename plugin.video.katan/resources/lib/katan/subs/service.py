"""The Kodi subtitle dialog provider.

Kodi calls this when the viewer opens the subtitle list during playback. It is
the manual counterpart to auto.py: the same candidates, the same scoring, but
shown as a list with the match score visible so the viewer can decide.
"""
import time

try:
    from urllib.parse import parse_qsl, urlencode
except ImportError:      # pragma: no cover
    from urllib import urlencode
    from urlparse import parse_qsl

import xbmc
import xbmcgui
import xbmcplugin
import xbmcvfs

from .. import kodi, settings
from . import auto, embedded, matcher, srt, sync

# Kodi shows five stars; the score is a percentage.
STARS = 5

# The synthetic entry that is not a subtitle anyone has, but one we can make.
AI_PROVIDER = "ai"

# The two window properties this and the background service talk over. They
# are properties rather than module variables because Kodi runs each plugin
# call in its own Python process, so a module variable would be a fresh empty
# one every time.
AI_REQUEST = "subs.translate"       # the dialog asks; the service answers
AI_RUNNING = "subs.translating"     # set while one is under way


def dispatch(argv):
    """Run one subtitle action, and close the directory whatever happens.

    Closing it is the only thing that ends Kodi's "searching for subtitles"
    dialog. Leaving it to each branch meant a provider that raised - a network
    error, a payload that changed shape - left the viewer looking at a spinner
    with no way out but to close the dialog by hand. So the close lives here,
    in a finally, and nowhere else.
    """
    handle = int(argv[1]) if len(argv) > 1 else -1
    params = dict(parse_qsl((argv[2] if len(argv) > 2 else "").lstrip("?")))
    action = params.get("action", "")

    try:
        if action in ("search", "manualsearch"):
            _search(handle, params)
        elif action == "download":
            _download(handle, params)
    except Exception:
        kodi.log_exception("subtitle action %r failed" % action)
        kodi.notify(kodi.localize(32336))
    finally:
        xbmcplugin.endOfDirectory(handle)


def _current_meta():
    """What is playing, taken from the player when this add-on started it."""
    from .. import player

    meta = player.now_playing()
    if meta:
        return meta

    # Something else is playing; fall back to what Kodi knows.
    return {
        "type": "episode" if xbmc.getInfoLabel("VideoPlayer.TVShowTitle") else "movie",
        "ids": {"imdb": xbmc.getInfoLabel("VideoPlayer.IMDBNumber")},
        "title": (xbmc.getInfoLabel("VideoPlayer.TVShowTitle")
                  or xbmc.getInfoLabel("VideoPlayer.Title")),
        "year": _int(xbmc.getInfoLabel("VideoPlayer.Year")),
        "season": _int(xbmc.getInfoLabel("VideoPlayer.Season")),
        "episode": _int(xbmc.getInfoLabel("VideoPlayer.Episode")),
        "stream_url": xbmc.Player().getPlayingFile() if xbmc.Player().isPlaying() else "",
    }


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _search(handle, params):
    """List what is available, best first.

    The order is a hierarchy, not a score sort:

        embedded tracks      already in the file, so exactly in time
        exact matches        the same release, or the same file by hash
        everything else      ranked by how well the release name correlates

    Embedded tracks are listed first and without any network call, because
    Kodi has already demuxed the file it is playing.
    """
    meta = _current_meta()
    languages = _search_languages(params)

    inside = embedded.candidates(languages)

    video_hash = auto.video_hash_for(meta)
    found = auto.search_candidates(meta, languages, video_hash)
    target = matcher.target_from(meta)
    ranked = matcher.rank(found, target, video_hash, languages) if found else []

    entries = inside + ranked
    offer = _ai_entry(ranked, _target_language(languages))
    if offer:
        entries = entries + [offer]
    if not entries:
        kodi.notify(kodi.localize(32336))
        return

    for position, candidate in enumerate(entries[:40]):
        _add(handle, position, candidate)


def _target_language(languages):
    """The language a translation should produce.

    Kodi's own subtitle language leads, because the viewer set it in the
    dialog they are standing in and that is the clearest statement of what
    they want right now. Falling back to this add-on's own preference matters
    on a stock Kodi, which asks for English whatever the interface language.
    """
    return (languages or settings.subtitle_languages() or ["he"])[0]


def _ai_entry(ranked, target):
    """The "translate this with AI" row, or None when there is no engine.

    It is offered whether or not anything was found, and that is the point.
    Offering it only when the list is empty would miss the case the viewer
    actually complains about - subtitles that exist, are in the right
    language, and are wrong - and there is no way for this add-on to tell a
    good subtitle from a bad one by looking at it. So the row is always there
    and the viewer decides.

    It is listed last, because a real subtitle in the right language is
    usually the better answer and takes seconds rather than minutes.

    The row appears without a key configured, and says so. Hiding it until a
    key exists means the one viewer who most needs it - the one staring at a
    film with no subtitles - is shown nothing and has no way to find out the
    feature is there. Switching AI off in the settings does hide it, because
    that is somebody saying they do not want it rather than not having got to
    it yet.
    """
    from .ai import translator

    if not settings.get_bool("subs.ai.enabled"):
        return None
    ready = translator.available()

    # Label it with the source it would most likely use, when the search just
    # found one. It is a guess - the download path searches wider than this
    # list does - so it is only shown when we have something to point at.
    source = ""
    for candidate in ranked or []:
        language = candidate.get("language", "")
        if language and language != target:
            source = language
            break

    if not ready:
        detail = kodi.localize(32497)
    elif source:
        detail = kodi.localize(32492, source.upper())
    else:
        detail = kodi.localize(32493)

    return {
        "provider": AI_PROVIDER,
        "language": target,
        "score": 0,
        "ai": True,
        "release": detail,
    }


def _search_languages(params):
    """What Kodi asked for, plus what this add-on is configured for.

    Kodi ships with its subtitle language set to English, and that is what it
    passes here. Wizdom is a Hebrew site, so a Hebrew viewer on a stock Kodi
    opened the subtitle list in a Hebrew add-on and was told "no subtitles
    found" - which was true of the question asked and useless as an answer.

    The union rather than a replacement, and in Kodi's order: what the viewer
    explicitly asked Kodi for still comes first and is never dropped, and the
    add-on's own preference is added rather than imposed. The automatic path
    is unaffected; it has always used this add-on's setting directly.
    """
    languages = _requested_languages(params)
    for code in settings.subtitle_languages():
        if code not in languages:
            languages.append(code)
    return languages


def _requested_languages(params):
    """Kodi passes the wanted languages as English names."""
    raw = params.get("languages", "")
    if not raw:
        return []
    codes = []
    for name in raw.split(","):
        code = xbmc.convertLanguage(name.strip(), xbmc.ISO_639_1)
        if code and code not in codes:
            codes.append(code)
    return codes


def match_label(candidate):
    """How well this subtitle fits, in the words the viewer needs.

    An embedded track says so, because that is the reason to pick it. An exact
    match says 100% with nothing else to explain. Everything else shows the
    estimate, so a 62% is visibly a guess rather than a promise.
    """
    if candidate.get("ai"):
        return kodi.localize(32494)

    score = int(round(candidate.get("score") or 0))

    if candidate.get("embedded"):
        if candidate.get("partial"):
            return "%d%% %s" % (score, kodi.localize(32402))
        return "%d%% %s" % (score, kodi.localize(32400))

    if score >= 100:
        return "100%"

    return "%d%% %s" % (score, kodi.localize(32401))


def _add(handle, position, candidate):
    score = candidate.get("score", 0)
    # Three of five for the AI row, and deliberately not a computed number.
    # Its accuracy is the accuracy of whatever it ends up translating, which
    # is not known until it has run, so any figure here would be invented.
    stars = ("3" if candidate.get("ai")
             else str(max(1, min(STARS, int(round(score / 100.0 * STARS))))))

    parts = [match_label(candidate)]
    release = candidate.get("release") or candidate.get("provider", "")
    if release:
        parts.append(release)
    if candidate.get("reason") and not candidate.get("embedded"):
        parts.append("[%s]" % candidate["reason"])
    label2 = "  ".join(parts)

    item = xbmcgui.ListItem(label=candidate.get("language", ""),
                            label2=label2, offscreen=True)
    item.setArt({"icon": stars, "thumb": candidate.get("language", "")})
    # Kodi shows a "sync" badge for subtitles known to match the file exactly.
    exact = candidate.get("embedded") or candidate.get("reason") == "hash"
    item.setProperty("sync", "true" if exact else "false")
    item.setProperty("hearing_imp",
                     "true" if candidate.get("hearing_impaired") else "false")

    url = "plugin://plugin.video.katan/?%s" % urlencode({
        "action": "download",
        "provider": candidate.get("provider", ""),
        "id": str(candidate.get("download", "")),
        "language": candidate.get("language", ""),
        "release": candidate.get("release", ""),
        "extra_movie": candidate.get("extra_movie", ""),
    })
    xbmcplugin.addDirectoryItem(handle, url, item, isFolder=False)


def _download(handle, params):
    """Fetch the chosen subtitle, clean it, and hand the path back to Kodi."""
    candidate = {
        "provider": params.get("provider", ""),
        "download": params.get("id", ""),
        "language": params.get("language", ""),
        "release": params.get("release", ""),
        "extra_movie": params.get("extra_movie", ""),
    }

    if candidate["provider"] == AI_PROVIDER:
        _translate_with_ai(candidate["language"])
        return

    # An embedded track is switched on in the player. There is no file to hand
    # back, so the directory is closed empty and Kodi keeps what it has.
    if candidate["provider"] == embedded.PROVIDER:
        if embedded.select(candidate["download"]):
            kodi.notify(kodi.localize(32350))
        return

    cues = auto.download_candidate(candidate)
    if not cues:
        kodi.notify(kodi.localize(32336))
        return

    meta = _current_meta()
    cues = _sync_against_embedded(cues)
    path = auto.store(meta, candidate["language"] or "und", cues)
    if path:
        item = xbmcgui.ListItem(label=path)
        xbmcplugin.addDirectoryItem(handle, path, item, isFolder=False)


def _sync_against_embedded(cues):
    """Re-time a manual pick when a reference subtitle is already loaded.

    If the viewer already has a working subtitle applied, its timings are a
    reference for the new one, so a manual pick benefits from the same
    correction the automatic path gets.
    """
    reference_path = _active_subtitle_path()
    if not reference_path:
        return cues
    try:
        reference = srt.read(reference_path)
    except Exception:
        return cues
    if not reference:
        return cues
    fitted, report = sync.synchronise(cues, reference)
    if report["applied"]:
        kodi.log("manual subtitle re-timed by %.2fs" % report["offset"])
    return fitted


def _active_subtitle_path():
    for name in ("VideoPlayer.SubtitlesPath", "Player.Filename"):
        value = xbmc.getInfoLabel(name)
        if value and value.lower().endswith(".srt") and xbmcvfs.exists(value):
            return value
    return ""


def _translate_with_ai(language):
    """Ask the background service to translate, and return immediately.

    Doing it here does not work, and both reasons were found in a real Kodi
    rather than reasoned about.

    A plugin invocation is torn down the moment it returns - the player
    monitor lives in the service for exactly this reason - so a thread started
    here would be killed part way through a translation that takes minutes.
    And Kodi's subtitle window is *modal*, so the API key prompt opened from
    under it never appears at all: the same rule that stopped a context menu
    starting playback until the busy dialog was closed. The service is neither
    of those things. It outlives every window and every plugin call, and it
    can wait for the dialog to close before it asks for anything.
    """
    if kodi.get_property(AI_RUNNING) == "1":
        kodi.notify(kodi.localize(32495))
        return
    kodi.set_property(
        AI_REQUEST, language or _target_language(settings.subtitle_languages()))


def take_request():
    """The language the dialog asked for, taken rather than read.

    Called by the service on its own single thread, so taking it is what makes
    sure one press cannot start two translations.
    """
    target = kodi.get_property(AI_REQUEST)
    if target:
        kodi.clear_property(AI_REQUEST)
    return target


def run_translation(target):
    """Do what the dialog asked for. Runs on the service's own thread."""
    import xbmc

    if kodi.get_property(AI_RUNNING) == "1":
        kodi.notify(kodi.localize(32495))
        return ""
    kodi.set_property(AI_RUNNING, "1")
    try:
        _close_the_dialog()
        if not _engine_ready():
            return ""
        kodi.notify(kodi.localize(32496))
        path = auto.translate_now(_current_meta(), target, xbmc.Player())
        kodi.notify(kodi.localize(32334, kodi.localize(32494)) if path
                    else kodi.localize(32336))
        return path
    except Exception:
        kodi.log_exception("AI subtitle translation failed")
        kodi.notify(kodi.localize(32336))
        return ""
    finally:
        kodi.clear_property(AI_RUNNING)


def _engine_ready():
    """Make sure there is something to translate with, asking if there is not.

    Picking the row is the clearest possible statement that the viewer wants
    AI translation, so it is the right moment to ask for the key rather than
    telling them to go and find a settings page. The wizard already owns that
    conversation, including checking the key actually works.
    """
    from .ai import translator

    if translator.available():
        return True

    from ..ui import wizard
    wizard.step_ai()

    if translator.available():
        return True
    kodi.notify(kodi.localize(32497))
    return False


def _close_the_dialog(seconds=5):
    """Put Kodi's subtitle list away, and wait until it has actually gone.

    Two reasons, and the first was measured. It is a modal dialog, and Kodi
    refuses to open a window over a modal - silently, the new window simply
    never appearing - so the API key prompt asked for from under it did
    nothing at all. Kodi closes the list itself when a subtitle is handed
    back, but this row hands nothing back: there is no file yet, only a
    promise to make one, so the list stayed up.

    The second is what the viewer wants. They picked a row; the list has done
    its job, and the translation appears on the film behind it. Leaving the
    list on top of the thing it is writing to would be a strange place to
    leave somebody.
    """
    import xbmc

    if not xbmc.getCondVisibility("Window.IsActive(subtitlesearch)"):
        return True
    xbmc.executebuiltin("Dialog.Close(subtitlesearch)")
    monitor = xbmc.Monitor()
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not xbmc.getCondVisibility("Window.IsActive(subtitlesearch)"):
            return True
        if monitor.waitForAbort(0.2):
            return False
    kodi.log("the subtitle list would not close")
    return False
