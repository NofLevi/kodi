"""Choosing and applying one subtitle when playback starts.

The order is deliberate, and each step is cheaper than the one after it:

1. A Hebrew track already inside the file. Free, and perfectly timed.
2. A file-hash match. Two range requests, and a guaranteed correct timing.
3. The best release-name correlation above the threshold.
4. AI translation of the best English match, which inherits its timings.

The pipeline stops at the first step that succeeds, so a normal playback
downloads one small file and often none at all. When a trustworthy reference
exists, the chosen subtitle is verified and re-timed against it, which turns
"probably the right subtitle" into "demonstrably fits".
"""
import os
import time

from .. import http, kodi, settings
from . import embedded, hasher, matcher, srt, sync

CACHE_DIR = "subtitles"



def on_playback_started(player, meta):
    """Entry point called by the player monitor."""
    if not settings.get_bool("subs.auto"):
        return
    languages = settings.subtitle_languages()
    if not languages:
        return
    wanted = languages[0]

    if settings.get_bool("subs.embedded_first") and use_embedded(player, wanted):
        kodi.notify(kodi.localize(32350))
        return

    path = cached_subtitle(meta, wanted)
    if path:
        apply_subtitle(player, path)
        return

    kodi.log("looking for %s subtitles" % wanted)
    path, report = find_and_prepare(meta, languages, player)
    if not path:
        kodi.log("no usable subtitle was found: %s" % report.get("reason"))
        return

    apply_subtitle(player, path)
    if report.get("translated"):
        kodi.notify(kodi.localize(32334, kodi.localize(32302)))
    elif report.get("synchronised"):
        kodi.notify(kodi.localize(32351))


# --------------------------------------------------------------------------
# embedded tracks
#
# The listing itself lives in embedded.py, which the subtitle chooser also
# uses. Keeping one implementation matters: two copies of "find the Hebrew
# track in this file" would answer differently the moment either changed.
# --------------------------------------------------------------------------


def use_embedded(player, language):
    """Select an embedded track in the wanted language, when there is one.

    A forced or signs-only track is skipped: it captions on-screen text rather
    than translating the dialogue, so choosing it looks like broken subtitles.
    """
    for candidate in embedded.candidates([language]):
        if candidate.get("partial"):
            continue
        if embedded.select(candidate["stream_index"]):
            return True
    return False


def embedded_languages(player=None):
    """Languages present inside the playing file."""
    seen = []
    for stream in embedded.streams():
        code = stream.get("language")
        if code and code not in seen:
            seen.append(code)
    return seen


# --------------------------------------------------------------------------
# searching
# --------------------------------------------------------------------------

_MODULES = {}


def _modules():
    if not _MODULES:
        from .providers import opensubtitles, subsource, wizdom
        _MODULES.update({"wizdom": wizdom, "opensubtitles": opensubtitles,
                         "subsource": subsource})
    return _MODULES


def _providers():
    """Enabled provider modules, in the order they should be asked.

    Wizdom leads because it is Hebrew-only and fast; OpenSubtitles follows
    because it is the one that can match on the file hash.
    """
    modules = _modules()
    enabled = settings.enabled_subtitle_providers()
    order = ["wizdom", "opensubtitles", "subsource"]
    return [(name, modules[name]) for name in order
            if name in enabled and name in modules]


def search_candidates(meta, languages, video_hash=""):
    """Ask every enabled provider at once, under a bounded worker pool."""
    providers = _providers()
    if not providers:
        return []
    target = matcher.target_from(meta)

    def make(name, module):
        def call():
            if name == "opensubtitles":
                return module.search(meta, target, languages, video_hash)
            return module.search(meta, target, languages)
        return call

    found = http.run_parallel(
        [(name, make(name, module)) for name, module in providers],
        workers=3, deadline=10.0)

    candidates = []
    for entries in found.values():
        candidates.extend(entries or [])
    return candidates


def find_and_prepare(meta, languages, player=None):
    """Find, download, verify and store one subtitle. Returns (path, report)."""
    report = {"translated": False, "synchronised": False, "reason": ""}
    wanted = languages[0]

    video_hash = video_hash_for(meta)
    candidates = search_candidates(meta, languages, video_hash)
    if not candidates:
        report["reason"] = "no candidates"
        return "", report

    target = matcher.target_from(meta)
    threshold = settings.get_int("subs.threshold", 70)
    winners, _ranked = matcher.best(candidates, target, threshold,
                                    video_hash, languages)

    winner = winners.get(wanted)
    if winner and winner.get("accepted"):
        cues = download_candidate(winner)
        if cues:
            cues, report = verify_and_sync(cues, winners, languages, report)
            report["reason"] = winner.get("reason", "")
            return store(meta, wanted, cues), report

    path, report = translate_fallback(meta, winners, languages, report, player)
    if path:
        return path, report

    # Last resort: the best available match, even below the threshold, because
    # an imperfect subtitle beats none and the viewer can still switch it off.
    if winner:
        cues = download_candidate(winner)
        if cues:
            cues, report = verify_and_sync(cues, winners, languages, report)
            report["reason"] = "below threshold, used anyway"
            return store(meta, wanted, cues), report

    report["reason"] = "nothing usable"
    return "", report


def video_hash_for(meta):
    if not settings.get_bool("subs.hash_match"):
        return ""
    url = meta.get("stream_url") or ""
    if not url:
        return ""
    started = time.time()
    value, _size = hasher.hash_stream(url)
    if value:
        kodi.log("file hash %s computed in %d ms"
                 % (value, (time.time() - started) * 1000))
    return value


def download_candidate(candidate):
    module = _modules().get(candidate.get("provider"))
    if module is None:
        return []
    try:
        data = module.download(candidate)
    except Exception:
        kodi.log_exception("downloading from %s failed" % candidate.get("provider"))
        return []
    if not data:
        return []
    cues = srt.parse(srt.decode(data))
    return srt.clean(cues) if cues else []


# --------------------------------------------------------------------------
# verification and re-timing
# --------------------------------------------------------------------------


def verify_and_sync(cues, winners, languages, report):
    """Check the chosen subtitle against a trusted reference, and re-time it.

    The reference is a hash-matched subtitle in another language. Its timings
    are known to be right for this exact file, so it doubles as proof that the
    chosen subtitle belongs to this episode at all: an unrelated file scores
    near zero however it is shifted, and is then rejected rather than shown.
    """
    reference = reference_cues(winners, languages)
    if not reference:
        return cues, report

    fitted, result = sync.synchronise(cues, reference)
    report["confidence"] = result["confidence"]
    if result["applied"]:
        report["synchronised"] = True
        kodi.log("subtitle re-timed by %.2fs, scale %.5f, confidence %.2f"
                 % (result["offset"], result["scale"], result["confidence"]))
    else:
        kodi.log("subtitle timing left alone: %s" % result["reason"])
    return fitted, report


def reference_cues(winners, languages):
    """A subtitle whose timings we know are correct for this file."""
    for language in languages[1:]:
        candidate = winners.get(language)
        if candidate and candidate.get("reason") == "hash":
            cues = download_candidate(candidate)
            if cues:
                kodi.log("using the %s hash match as a timing reference" % language)
                return cues
    return []


def translate_fallback(meta, winners, languages, report, player=None):
    """Translate the best other-language match into the wanted language.

    Timings come from the source subtitle and are never touched, so a
    translated file is exactly as well synchronised as the file it came from.

    Which source to translate from is not simply the best match. Hebrew marks
    the speaker's gender on verbs and adjectives, and a language that already
    marks it carries that through for free, so a Spanish or Arabic subtitle can
    beat a slightly better-matching English one.
    """
    from .ai import context as translation_context
    from .ai import translator

    if not translator.available():
        return "", report

    ranked = translation_context.rank_translation_sources(winners, languages)
    source = next(((lang, cand) for lang, cand in ranked
                   if (cand.get("score") or 0) >= 40), None)
    if not source:
        return "", report

    language, candidate = source
    cues = download_candidate(candidate)
    if not cues:
        return "", report

    translated = _translate_progressively(cues, meta, languages[0], player)
    if not translated:
        return "", report

    report["translated"] = True
    report["source_language"] = language
    return store(meta, languages[0], translated), report


def _translate_progressively(cues, meta, language, player):
    """Translate, showing each finished chunk as it arrives.

    A feature-length film is several minutes of translation. Waiting for all of
    it before showing anything means staring at a progress bar; showing the
    first chunk immediately means the viewer starts watching while the rest is
    still being written.

    Kodi caches a subtitle file by path, so re-writing the same name changes
    nothing on screen. Alternating between two names forces it to re-read.
    """
    import xbmcgui

    from .ai import translator

    progress = xbmcgui.DialogProgressBG()
    progress.create("Katan", kodi.localize(32335))
    slots = _partial_slots(meta, language)
    state = {"slot": 0, "shown": 0}

    def on_progress(done, total, partial=None):
        progress.update(int(done * 100 / max(1, total)),
                        message=kodi.localize(32335))
        if player is None or partial is None or done <= state["shown"]:
            return
        path = slots[state["slot"] % len(slots)]
        try:
            srt.write(path, partial)
            player.setSubtitles(path)
            player.showSubtitles(True)
            state["slot"] += 1
            state["shown"] = done
        except Exception:
            kodi.log_exception("could not show a partial translation")

    try:
        return translator.translate(cues, language, on_progress=on_progress,
                                    meta=meta)
    except translator.TranslationError:
        kodi.log_exception("AI translation failed")
        return []
    finally:
        progress.close()
        _clean_partials(slots)


def _partial_slots(meta, language):
    """Two file names to alternate between while translating."""
    base = name_for(meta, language)[:-4]
    directory = subtitle_dir()
    return [os.path.join(directory, "%s.part%s.srt" % (base, suffix))
            for suffix in ("a", "b")]


def _clean_partials(slots):
    for path in slots:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------


def subtitle_dir():
    return kodi.subdir(CACHE_DIR)


def name_for(meta, language):
    ids = meta.get("ids") or {}
    key = str(ids.get("imdb") or ids.get("tmdb") or (meta.get("title") or "x"))
    key = "".join(c for c in key if c.isalnum() or c in "-_")[:40] or "x"
    if meta.get("type") == "episode":
        return "%s.s%02de%02d.%s.srt" % (key, int(meta.get("season") or 0),
                                         int(meta.get("episode") or 0), language)
    return "%s.%s.srt" % (key, language)


def cached_subtitle(meta, language):
    """A subtitle prepared earlier for this exact item."""
    path = os.path.join(subtitle_dir(), name_for(meta, language))
    return path if os.path.isfile(path) else ""


def store(meta, language, cues):
    if not cues:
        return ""
    path = os.path.join(subtitle_dir(), name_for(meta, language))
    srt.write(path, cues)
    prune_cache()
    return path


def prune_cache():
    """Keep the subtitle folder within the configured file count."""
    limit = max(10, settings.get_int("subs.cache_files", 60))
    directory = subtitle_dir()
    try:
        names = [n for n in os.listdir(directory) if n.endswith(".srt")]
        entries = [(os.path.getmtime(os.path.join(directory, n)),
                    os.path.join(directory, n)) for n in names]
    except OSError:
        return
    if len(entries) <= limit:
        return
    entries.sort()
    for _stamp, path in entries[:len(entries) - limit]:
        try:
            os.remove(path)
        except OSError:
            pass


def apply_subtitle(player, path):
    try:
        player.setSubtitles(path)
        player.showSubtitles(True)
    except Exception:
        kodi.log_exception("could not apply the subtitle file")
