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
import hashlib
import os
import threading
import time

from .. import http, kodi, settings
from . import embedded, hasher, matcher, srt, sync

CACHE_DIR = "subtitles"

# Marks a subtitle this add-on wrote rather than downloaded.
VARIANT_AI = "ai"

# Languages worth translating *out of* when the wanted one cannot be found.
# Ordered by how much the subtitle catalogues actually hold, and asking for
# them costs nothing extra - it is one more value in the same request, and the
# only provider that reads it is OpenSubtitles. Wizdom and Ktuvit are Hebrew
# sites and ignore it.
WIDE_LANGUAGES = ("en", "es", "ar", "pt", "fr", "ru", "de", "it", "tr", "pl")
TIMING_EVIDENCE_LANGUAGES = ("en", "es")


def search_timing_evidence(meta, languages, video_hash=""):
    """Optional third-language evidence under its own disposable deadline.

    The primary search has already completed before this runs, so a slow
    evidence request can be dropped without losing a usable Hebrew result.
    Hash search is deliberately omitted: this asks one cheap title query for
    the one missing language, while the primary languages already used hash.
    """
    missing = [language for language in TIMING_EVIDENCE_LANGUAGES
               if language not in languages]
    if not missing:
        return []
    from .providers import opensubtitles_rest

    def search():
        return opensubtitles_rest.search(meta, matcher.target_from(meta),
                                         missing[:1], "", 0)

    found = http.run_parallel([("timing-evidence", search)], workers=1,
                              deadline=4.0)
    return found.get("timing-evidence") or []



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
        kodi.notify(kodi.localize(32334, kodi.localize(32494)))
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


# --------------------------------------------------------------------------
# searching
# --------------------------------------------------------------------------

_MODULES = {}


def _modules():
    if not _MODULES:
        from .providers import (bsplayer, ktuvit, opensubtitles,
                                opensubtitles_rest, subsource, wizdom)
        _MODULES.update({"wizdom": wizdom, "opensubtitles": opensubtitles,
                         "opensubtitles_rest": opensubtitles_rest,
                         "bsplayer": bsplayer,
                         "subsource": subsource, "ktuvit": ktuvit})
    return _MODULES


def _providers():
    """Enabled provider modules, in the order they should be asked.

    Wizdom leads because it is Hebrew-only and fast. Then the two
    OpenSubtitles: the anonymous legacy search first, because it needs no
    account and is the only thing standing between an English-language series
    and no subtitle at all, then the modern one, which can match on the file
    hash but needs a key whose free tier is five downloads a day. Ktuvit is
    last of the Hebrew sources despite having the best catalogue, because it
    is the only one that needs a signed-in session and so the only one that
    can be slow for a reason the user cannot see.
    """
    modules = _modules()
    enabled = settings.enabled_subtitle_providers()
    order = ["wizdom", "bsplayer", "opensubtitles_rest", "opensubtitles",
             "ktuvit", "subsource"]
    return [(name, modules[name]) for name in order
            if name in enabled and name in modules]


def search_candidates(meta, languages, video_hash=""):
    """Ask every enabled provider at once, under a bounded worker pool.

    `video_hash` may be a string or a callable that will produce one, which is
    what lets the hash be computed alongside this search rather than before it.
    """
    providers = _providers()
    if not providers:
        return []
    target = matcher.target_from(meta)

    def make(name, module):
        def call():
            # Resolved inside the task, so the nine providers that never look
            # at a hash are already asking while it is still being computed.
            if name == "opensubtitles":
                return module.search(meta, target, languages,
                                     _hash_value(video_hash))
            if name == "opensubtitles_rest":
                # It can answer a hash query too, and that is where the only
                # score-100 evidence comes from.
                found_hash = _hash_value(video_hash)
                return module.search(meta, target, languages, found_hash,
                                     meta.get("stream_size") or 0)
            if name == "bsplayer":
                # Hash *and* size: the service answers HTTP 500 to an empty
                # hash rather than returning nothing, so the provider checks
                # both and asks nothing when it has neither.
                found_hash = _hash_value(video_hash)
                return module.search(meta, target, languages, found_hash,
                                     meta.get("stream_size") or 0)
            return module.search(meta, target, languages)
        return call

    found = http.run_parallel(
        [(name, make(name, module)) for name, module in providers],
        workers=3, deadline=10.0)

    candidates = []
    seen = set()
    for entries in found.values():
        for candidate in entries or []:
            # One subtitle can arrive twice: asking by hash and asking by
            # title are different questions with overlapping answers, and two
            # providers can serve the same file. A duplicate costs more than
            # it looks - `outlook` weighs *every* candidate against *every*
            # source when the picker opens, so one extra candidate is one
            # extra comparison per source, and this add-on exists to run on a
            # projector with a gigabyte of RAM.
            key = candidate.get("download") or ""
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            candidates.append(candidate)
    return candidates


def find_and_prepare(meta, languages, player=None):
    """Find, download, verify and store one subtitle. Returns (path, report)."""
    report = {"translated": False, "synchronised": False, "reason": ""}
    wanted = languages[0]

    get_hash = video_hash_later(meta)
    candidates = search_candidates(meta, languages, get_hash)
    # By here every provider that wanted it has already waited, so this is a
    # value rather than a wait.
    video_hash = get_hash()
    if not candidates:
        # Nothing in either configured language is not the same as nothing at
        # all. Before reporting a film as having no subtitles, ask the whole
        # catalogue and translate whatever it does have - which for anything
        # outside the mainstream is the difference between watching it and
        # not.
        return _last_resort(meta, languages, report, player, video_hash,
                            "no candidates")

    target = matcher.target_from(meta)
    threshold = settings.get_int("subs.threshold", 70)
    winners, _ranked = matcher.best(candidates, target, threshold,
                                    video_hash, languages)

    winner = winners.get(wanted)
    if winner and winner.get("accepted"):
        from . import consensus
        has_hash_reference = any(
            winners.get(language, {}).get("reason") == "hash"
            for language in languages[1:])
        if consensus.wanted(int(winner.get("score") or 0),
                            has_hash_reference):
            evidence = search_timing_evidence(meta, languages, video_hash)
            seen = {candidate.get("download") for candidate in candidates}
            candidates.extend(candidate for candidate in evidence
                              if candidate.get("download") not in seen)
            winners, _ranked = matcher.best(candidates, target, threshold,
                                            video_hash, languages)
            winner = winners.get(wanted)
    if winner:
        kodi.log("best %s subtitle: %s %r"
                 % (wanted, matcher.explain(winner),
                    (winner.get("release") or "")[:60]))
    if winner and winner.get("accepted"):
        winner, cues, report = _best_supported(
            winner, _ranked, wanted, languages, winners, report)
        if cues:
            cues, report = verify_and_sync(cues, winners, languages, report)
            report["reason"] = report.get("reason") or winner.get("reason", "")
            return store(meta, wanted, cues), report

    path, report = translate_fallback(meta, winners, languages, report, player)
    if path:
        return path, report

    # Last resort: the best available match, even below the threshold, because
    # an imperfect subtitle beats none and the viewer can still switch it off.
    if winner:
        cues = download_candidate(winner, expect_language=wanted)
        if cues:
            cues, report = verify_and_sync(cues, winners, languages, report)
            report["reason"] = "below threshold, used anyway"
            return store(meta, wanted, cues), report

    return _last_resort(meta, languages, report, player, video_hash,
                        "nothing usable")


def _last_resort(meta, languages, report, player, video_hash, reason):
    """Translate out of any language at all, having found nothing to show.

    Separate from `translate_fallback` because the two answer different
    questions. That one asks "is there a better route than this mediocre
    Hebrew match", and quite reasonably wants a source that matches well. This
    one is asked when there is no route at all, so it takes what it can get.
    """
    path = translate_now(meta, languages[0], player, video_hash=video_hash)
    if path:
        report["translated"] = True
        report["reason"] = "translated, nothing was available to download"
        return path, report
    report["reason"] = reason
    return "", report


# How long a provider will wait for the hash before asking without it. The
# whole search has a ten second deadline, so this has to leave room for the
# request that follows it.
HASH_WAIT = 5.0


def video_hash_later(meta):
    """Start computing the file hash now and return a callable that waits.

    The hash used to be computed first and on its own, so its whole latency -
    a HEAD and two 64 KB ranged requests against the debrid CDN, about a
    second - was added to every subtitle search. That second is not a black
    screen, it is a film already playing with no subtitles on it, which is
    worse to sit through than it sounds.

    It is worth paying, because a hash match is the only ruler this add-on
    owns: measured over 147 titles it is what delivers a perfect subtitle 3%
    of the time and what supplies a timing reference the other 7%, and four of
    those references caught a name-matched subtitle that did not fit at all.
    But it does not have to be paid *in series*. Three of a dozen providers
    want the hash; the rest can be asked immediately, and the slowest of them
    takes longer than the hash does.

    One thread, not one per provider - the pool in `search_candidates` cannot
    host this, because a task waiting inside the pool for another task in the
    same pool deadlocks the moment every worker is a waiter.
    """
    if not settings.get_bool("subs.hash_match") or not meta.get("stream_url"):
        return lambda: ""

    holder = {}

    def work():
        try:
            holder["value"] = video_hash_for(meta)
        except Exception:
            kodi.log_exception("hashing the stream failed")
            holder["value"] = ""

    thread = threading.Thread(target=work)
    thread.daemon = True
    thread.start()

    def get():
        thread.join(HASH_WAIT)
        if "value" not in holder:
            kodi.log("the file hash was not ready in %.0fs; asking without it"
                     % HASH_WAIT)
        return holder.get("value", "")

    return get


def _hash_value(video_hash):
    """Accept either a hash or something that will produce one."""
    return (video_hash() if callable(video_hash) else video_hash) or ""


def video_hash_for(meta):
    if not settings.get_bool("subs.hash_match"):
        return ""
    url = meta.get("stream_url") or ""
    if not url:
        return ""
    started = time.time()
    value, size = hasher.hash_stream(url)
    # Kept, not discarded. BSPlayer matches on the pair and this is the only
    # place the size is known - computing it again would mean a second HEAD
    # against the stream for a number already in hand.
    meta["stream_size"] = size
    if value:
        kodi.log("file hash %s computed in %d ms"
                 % (value, (time.time() - started) * 1000))
    return value


def download_candidate(candidate, expect_language=None):
    """Fetch one subtitle and turn it into cues.

    `expect_language` is checked only when the add-on chose the file itself.
    A subtitle listed as Hebrew and written in English is not rare - it is a
    mislabelled upload - and applying it silently gives the viewer the wrong
    language with no clue why. srt.looks_hebrew existed for this and was never
    called.

    The manual chooser deliberately does not pass it. There the viewer picked
    that entry, and second-guessing them would be worse than honouring a bad
    choice they can see and change.
    """
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
    if not cues:
        return []
    cues = srt.clean(cues)
    if expect_language == "he" and cues and not srt.looks_hebrew(cues):
        kodi.log("%s offered a Hebrew subtitle that is not in Hebrew (%s)"
                 % (candidate.get("provider"), candidate.get("release", "")[:60]))
        return []
    return cues


# --------------------------------------------------------------------------
# verification and re-timing
# --------------------------------------------------------------------------


def _best_supported(winner, candidates, wanted, languages, winners, report):
    """The chosen subtitle, checked against its rivals when nothing else can.

    A hash match settles this and is available 9% of the time. The other 91%
    is decided by how much a filename resembles the release, and that predicts
    timing poorly - measured, a score of 77 that was 176 seconds out and a
    score of 62 that was perfect. Two subtitles from different uploaders that
    agree on timing are almost certainly both right; the one that disagrees
    with both is wrong. That is knowable with no hash and no video.

    Falls back to the winner in every failure path, because this is a
    tie-breaker and never a gate.
    """
    from . import consensus

    has_reference = bool(reference_cues(winners, languages))
    if not consensus.wanted(int(winner.get("score") or 0), has_reference):
        return winner, download_candidate(winner, expect_language=wanted), report

    picks = consensus.verification_candidates(candidates, wanted,
                                               consensus.budget())
    if len(picks) < 2:
        return winner, download_candidate(winner, expect_language=wanted), report

    fetched = []
    for candidate in picks:
        expected = wanted if candidate.get("language") == wanted else None
        cues = download_candidate(candidate, expect_language=expected)
        if cues:
            fetched.append((candidate, cues))
    if not fetched:
        return winner, [], report

    # Two independently authored languages that agree provide a cheap timing
    # reference. Re-time the requested language against that proven timeline;
    # never return a foreign-language file merely because it formed the cluster.
    _reference_candidate, reference, evidence = consensus.timeline_reference(
        fetched, wanted)
    target = next(((candidate, cues) for candidate, cues in fetched
                   if candidate.get("language") == wanted), None)
    if target and reference:
        fitted, timing = sync.synchronise(target[1], reference)
        if timing["applied"] or timing["reason"] == "already in sync":
            report["timing_evidence"] = "cross-language consensus"
            report["supported"] = evidence["supported"]
            report["confidence"] = timing["confidence"]
            report["synchronised"] = timing["applied"]
            return target[0], fitted, report

    same_language = [(candidate, cues) for candidate, cues in fetched
                     if candidate.get("language") == wanted]
    chosen, cues, outcome = consensus.choose(same_language)
    report["consensus"] = outcome.get("reason", "")
    report["supported"] = outcome.get("supported", 0)
    if chosen is not None and chosen is not winner:
        kodi.log("consensus preferred %s over the top-scored %s: %s"
                 % (chosen.get("provider"), winner.get("provider"),
                    outcome.get("reason")))
    return chosen, cues, report


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


def wide_languages(target):
    """Every language we would accept as a translation source, target first.

    Deliberately wider than the automatic path asks for. That path is looking
    for something to *show*, so it asks for the two languages the viewer
    configured. This is looking for something to *translate*, and any language
    will do - a film with neither Hebrew nor English usually has a Spanish or
    an Arabic subtitle, and translating from one of those is a far better
    answer than "no subtitles found".
    """
    languages = [target]
    for code in list(settings.subtitle_languages()) + list(WIDE_LANGUAGES):
        if code and code not in languages:
            languages.append(code)
    return languages


def translation_sources(meta, target, video_hash="", candidates=None):
    """What could be translated into `target`, best source first.

    `candidates` lets a caller that has already searched hand its results in
    rather than paying for a second search.
    """
    from .ai import context as translation_context

    languages = wide_languages(target)
    if candidates is None:
        candidates = search_candidates(meta, languages, video_hash)
    if not candidates:
        return []
    winners, _ranked = matcher.best(candidates, matcher.target_from(meta),
                                    settings.get_int("subs.threshold", 70),
                                    video_hash, languages)
    return translation_context.rank_translation_sources(winners, [target])


def translate_now(meta, target, player=None, candidates=None, video_hash=None):
    """Translate the best subtitle we can find into `target`, and return its path.

    This is the viewer saying "what I have is not good enough" - or that there
    is nothing at all - so it ignores the acceptance threshold entirely and
    does not care whether a subtitle already exists in the target language.
    Its whole job is to produce one.

    Timings come from the source subtitle and are never touched, so the result
    fits exactly as well as the file it was translated from. Each finished
    chunk goes on screen as it arrives, because a feature film is minutes of
    translation and nobody should watch a progress bar for it.

    A source that turns out to be undownloadable does not end the attempt; the
    next best language is tried, which matters most in exactly the case this
    exists for - the obscure title where the one English subtitle listed is a
    dead link.
    """
    from .ai import translator

    if not translator.available():
        kodi.log("AI translation was asked for but no engine is configured")
        return ""

    if video_hash is None:
        video_hash = video_hash_for(meta)
    sources = translation_sources(meta, target, video_hash, candidates)
    if not sources:
        kodi.log("found nothing at all to translate into %s" % target)
        return ""

    for language, candidate in sources:
        cues = download_candidate(candidate)
        if not cues:
            continue
        kodi.log("translating the %s subtitle %r into %s"
                 % (language, (candidate.get("release") or "")[:60], target))
        translated = _translate_progressively(cues, meta, target, player,
                                              variant=VARIANT_AI)
        if translated:
            return store(meta, target, translated, variant=VARIANT_AI)
    kodi.log("every translation source failed to download")
    return ""


def _translate_progressively(cues, meta, language, player, variant=""):
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
    slots = _partial_slots(meta, language, variant)
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


def _partial_slots(meta, language, variant=""):
    """Two file names to alternate between while translating."""
    base = name_for(meta, language, variant)[:-4]
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


def _filename_key(key):
    """An ASCII name for a title that may have none of its own.

    str.isalnum() is true for Hebrew letters, so the previous "strip to
    alphanumerics" stripped nothing from an Israeli title and wrote the Hebrew
    straight onto the filesystem. Android storage and Kodi's path handling are
    both fussier about that than Windows is, and a subtitle that cannot be
    written is a subtitle that never appears.

    Falling back to "x" would give every Hebrew title the same filename, so a
    title with no ASCII in it gets a hash of itself instead.

    Lowercased for the same reason it is stripped: Windows treats two names
    differing only in case as one file and Android treats them as two, so a
    key whose case ever varies would write one subtitle on a PC and two on a
    television box, and the second would never be found again.
    """
    safe = "".join(c for c in key.lower()
                   if (c.isalnum() and c.isascii()) or c in "-_")[:40]
    if safe:
        return safe
    return "t" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def name_for(meta, language, variant=""):
    """The name a prepared subtitle is stored under.

    `variant` marks a subtitle that was made rather than found - an AI
    translation - so it never overwrites a downloaded one and both can sit in
    the folder together. It goes *before* the language, because Kodi reads a
    subtitle's language from the last part before ``.srt`` and would otherwise
    decide the file was written in a language called "ai".
    """
    ids = meta.get("ids") or {}
    key = str(ids.get("imdb") or ids.get("tmdb") or (meta.get("title") or "x"))
    key = _filename_key(key)

    mark = ".%s" % variant if variant else ""
    if meta.get("type") == "episode":
        return "%s.s%02de%02d%s.%s.srt" % (key, int(meta.get("season") or 0),
                                           int(meta.get("episode") or 0),
                                           mark, language)
    return "%s%s.%s.srt" % (key, mark, language)


def cached_subtitle(meta, language, variant=""):
    """A subtitle prepared earlier for this exact item."""
    path = os.path.join(subtitle_dir(), name_for(meta, language, variant))
    return path if os.path.isfile(path) else ""


def store(meta, language, cues, variant=""):
    if not cues:
        return ""
    path = os.path.join(subtitle_dir(), name_for(meta, language, variant))
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
