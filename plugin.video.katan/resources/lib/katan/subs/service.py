"""The Kodi subtitle dialog provider.

Kodi calls this when the viewer opens the subtitle list during playback. It is
the manual counterpart to auto.py: the same candidates, the same scoring, but
shown as a list with the match score visible so the viewer can decide.
"""
import os

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
    if not entries:
        kodi.notify(kodi.localize(32336))
        return

    for position, candidate in enumerate(entries[:40]):
        _add(handle, position, candidate)


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
    stars = str(max(1, min(STARS, int(round(score / 100.0 * STARS)))))

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
