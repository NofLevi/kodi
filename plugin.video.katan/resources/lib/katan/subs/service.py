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
from . import auto, matcher, srt, sync

# Kodi shows five stars; the score is a percentage.
STARS = 5


def dispatch(argv):
    handle = int(argv[1]) if len(argv) > 1 else -1
    params = dict(parse_qsl((argv[2] if len(argv) > 2 else "").lstrip("?")))
    action = params.get("action", "")

    if action in ("search", "manualsearch"):
        _search(handle, params)
    elif action == "download":
        _download(handle, params)
    else:
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
    meta = _current_meta()
    languages = _requested_languages(params) or settings.subtitle_languages()

    video_hash = auto.video_hash_for(meta)
    candidates = auto.search_candidates(meta, languages, video_hash)
    if not candidates:
        kodi.notify(kodi.localize(32336))
        xbmcplugin.endOfDirectory(handle)
        return

    target = matcher.target_from(meta)
    ranked = matcher.rank(candidates, target, video_hash, languages)

    for position, candidate in enumerate(ranked[:40]):
        _add(handle, position, candidate)

    xbmcplugin.endOfDirectory(handle)


def _requested_languages(params):
    """Kodi passes the wanted languages as English names."""
    raw = params.get("languages", "")
    if not raw:
        return []
    codes = []
    for name in raw.split(","):
        code = xbmc.convertLanguage(name.strip(), xbmc.ISO_639_1)
        if code:
            codes.append(code)
    return codes


def _add(handle, position, candidate):
    score = candidate.get("score", 0)
    stars = str(max(1, min(STARS, int(round(score / 100.0 * STARS)))))

    label2 = candidate.get("release") or candidate.get("provider", "")
    if candidate.get("reason"):
        label2 = "%s  [%s]" % (label2, candidate["reason"])

    item = xbmcgui.ListItem(label=candidate.get("language", ""),
                            label2=label2, offscreen=True)
    item.setArt({"icon": stars, "thumb": candidate.get("language", "")})
    item.setProperty("sync", "true" if candidate.get("reason") == "hash" else "false")
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
    cues = auto.download_candidate(candidate)
    if not cues:
        kodi.notify(kodi.localize(32336))
        xbmcplugin.endOfDirectory(handle)
        return

    meta = _current_meta()
    cues = _sync_against_embedded(cues)
    path = auto.store(meta, candidate["language"] or "und", cues)
    if path:
        item = xbmcgui.ListItem(label=path)
        xbmcplugin.addDirectoryItem(handle, path, item, isFolder=False)
    xbmcplugin.endOfDirectory(handle)


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
