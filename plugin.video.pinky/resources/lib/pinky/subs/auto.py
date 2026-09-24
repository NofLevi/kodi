"""Choosing and applying one subtitle when playback starts.

Strictly in three parts, and each is tried to the end before the next:

1. Hebrew. The track inside the file, one saved from an earlier play, the
   best downloaded one that fits - checked against a file-hash match when
   there is one - and then the best there is below the threshold.
2. AI. Translated from the source that fits best, Arabic preferred for its
   gender marking, and then from anything at all. Only with an engine.
3. English. The track inside the file, which is how every other anime
   add-on shows subtitles at all, and then a downloaded one.

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
# Asked for when the viewer chooses AI translation themselves: any language
# will do, so every one worth translating from is searched.
WIDE_LANGUAGES = ("en", "es", "ar", "pt", "fr", "ru", "de", "it", "tr", "pl",
                  "ja", "ko", "zh")

# What an automatic AI translation is made from once no Hebrew subtitle fits:
# Arabic and English for everything, and the show's own language when it is
# one of these - a Turkish drama is asked for Turkish, a Korean one for Korean.
# Not all of them every time. Every language is another request per provider,
# and a Korean subtitle is worth nothing to an American film. See
# ai/context.source_bonus for the order they are preferred in.
# What an automatic Hebrew translation may be made from, asked only once no
# Hebrew subtitle fits - which for anime is always. Every one of these except
# English marks the speaker's gender, which is the whole reason the list is
# not just English: Hebrew marks it on verbs and adjectives, so a source that
# carries it saves the model a guess.
#
# It used to be Arabic and English alone, and `context.GENDER_MARKING` knew
# about nineteen languages that were never once asked for. Measured over five
# titles on 24 September 2026, subtitles found per language:
#
#     en 29   ar 32   es 20   ru 20   pl 26   fr 18   it 12   pt 15
#
# and the anime rows are where the difference is, because that is where
# English and Arabic are thinnest. Naruto Shippuden 8x14: Polish 6 and
# Russian 4 against English 2 and Arabic 3. Frieren 1x01: Spanish 3 and
# French 2 where Italian and Portuguese found one each. Hikaru no Go 2x02
# has exactly two subtitles in the world and one of them is Polish.
#
# Italian and Portuguese are left out on those numbers: they cost the same
# request and found least, and a list that grows without a reason is a
# search that takes longer on a device waiting to draw a picker.
AI_SOURCE_LANGUAGES = ("ar", "en", "pl", "es", "ru", "fr")
FOREIGN_SOURCE_LANGUAGES = ("zh", "fr", "ko", "es", "it", "tr", "ja")

# TMDB's codes that are not ISO 639-1. "cn" is its code for Cantonese, which
# subtitle sites and audio tracks file under Chinese.
_TMDB_LANGUAGE = {"cn": "zh"}


def normalise_language(code):
    """A title's original language as TMDB gives it, in the codes used here."""
    code = (code or "").strip().lower()
    return _TMDB_LANGUAGE.get(code, code)


def translation_source_languages(meta, already=()):
    """The languages an automatic translation should be made from, if any.

    Empty without a translation engine, because a subtitle it cannot use is
    only a wasted request.
    """
    try:
        from .ai import translator
        if not translator.available():
            return []
    except Exception:
        return []
    wanted = list(AI_SOURCE_LANGUAGES)
    original = normalise_language(meta.get("original_language"))
    # Only if it is not already one of them. French and Spanish joined the
    # base list, and a French film was then asked for French twice - two
    # identical requests per provider, and the same file offered twice in
    # the picker.
    if original in FOREIGN_SOURCE_LANGUAGES and original not in wanted:
        wanted.append(original)
    return [code for code in wanted if code not in already]
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



def _subs_mode(meta):
    return ((meta or {}).get("source") or {}).get("subs_mode") or ""


def on_playback_started(player, meta, cancelled=None):
    """Entry point called by the player monitor."""
    from .ai import coordinator

    external_cancelled = cancelled or (lambda: False)
    if external_cancelled() or not settings.get_bool("subs.auto"):
        return
    languages = settings.subtitle_languages()
    if not languages:
        return
    if _subs_mode(meta) == "english":
        # Chosen from the picker's English rows: English, whatever the
        # configured languages say, and whatever language the film is in.
        languages = ["en"]
    elif normalise_language(meta.get("original_language")) == languages[0]:
        # An Israeli film is already in Hebrew. Searching for a Hebrew
        # subtitle costs a round of requests and, when one is found, puts the
        # dialogue on screen twice. The chooser still lists them for anybody
        # who wants one - hard of hearing, or a noisy room.
        kodi.log("%s is in %s already, so no subtitle is applied"
                 % (meta.get("title") or "this title", languages[0]))
        return

    generation = coordinator.begin()

    def is_cancelled():
        return (external_cancelled()
                or not coordinator.current(generation))

    wanted = languages[0]

    if _subs_mode(meta) == "llm":
        # Chosen from the picker's AI rows: translate, and do not go looking
        # for Hebrew first - the viewer asked to see what the model makes of
        # this release. The picker has already fetched what to translate
        # from, so this asks nothing new.
        from . import outlook
        kodi.log("AI subtitles were chosen for this release")
        path = translate_now(meta, wanted, player,
                             candidates=outlook.translation_candidates(meta) or None,
                             cancelled=is_cancelled, generation=generation)
        if path and not is_cancelled():
            kodi.notify(kodi.localize(32334, kodi.localize(32494)))
        return

    if settings.get_bool("subs.embedded_first"):
        committed, selected = coordinator.commit(
            generation, lambda: use_embedded(player, wanted, is_cancelled))
        if committed and selected:
            kodi.notify(kodi.localize(32350))
            return

    path = cached_subtitle(meta, wanted)
    if path:
        coordinator.commit(generation, lambda: apply_subtitle(player, path))
        return

    kodi.log("looking for %s subtitles" % wanted)

    def embedded_track(code):
        committed, selected = coordinator.commit(
            generation, lambda: use_embedded(player, code, is_cancelled))
        return committed and selected

    path, report = find_and_prepare(meta, languages, player,
                                    cancelled=is_cancelled,
                                    translation_generation=generation,
                                    embedded=embedded_track)
    if report.get("embedded") and not is_cancelled():
        kodi.notify(kodi.localize(32350))
        return
    if is_cancelled() or not path:
        kodi.log("no usable subtitle was found: %s" % report.get("reason"))
        return

    if not report.get("applied"):
        committed, _result = coordinator.commit(
            generation, lambda: apply_subtitle(player, path))
        if not committed:
            return
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


def use_embedded(player, language, cancelled=None):
    """Select a full embedded track only while this playback remains current."""
    cancelled = cancelled or (lambda: False)
    for candidate in embedded.candidates([language]):
        if candidate.get("partial"):
            continue
        if cancelled():
            return False
        if embedded.select(candidate["stream_index"], player):
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


def search_candidates(meta, languages, video_hash="", split_languages=False):
    """Ask every enabled provider at once, under a bounded worker pool.

    `video_hash` may be a string or a callable that will produce one, which is
    what lets the hash be computed alongside this search rather than before it.
    """
    providers = _providers()
    if not providers:
        return []
    memo_key = _search_memo_key(meta, languages, video_hash, split_languages)
    remembered = _recall_search(memo_key)
    if remembered is not None:
        return remembered
    target = matcher.target_from(meta)

    def make(name, module, asked_languages=None):
        asked_languages = asked_languages or languages

        def call():
            # Resolved inside the task, so the nine providers that never look
            # at a hash are already asking while it is still being computed.
            if name == "opensubtitles":
                return module.search(meta, target, asked_languages,
                                     _hash_value(video_hash),
                                     meta.get("stream_size") or 0)
            if name == "opensubtitles_rest":
                # It can answer a hash query too, and that is where the only
                # score-100 evidence comes from.
                found_hash = _hash_value(video_hash)
                return module.search(meta, target, asked_languages, found_hash,
                                     meta.get("stream_size") or 0)
            if name == "bsplayer":
                # Hash *and* size: the service answers HTTP 500 to an empty
                # hash rather than returning nothing, so the provider checks
                # both and asks nothing when it has neither.
                found_hash = _hash_value(video_hash)
                return module.search(meta, target, asked_languages, found_hash,
                                     meta.get("stream_size") or 0)
            return module.search(meta, target, asked_languages)
        return call

    tasks = []
    for name, module in providers:
        if split_languages and name == "opensubtitles_rest":
            tasks.extend(("%s:%s" % (name, language),
                          make(name, module, [language]))
                         for language in languages)
        else:
            tasks.append((name, make(name, module)))
    found = http.run_parallel(tasks, workers=3, deadline=10.0)

    candidates = []
    seen = set()
    for entries in found.values():
        for candidate in entries or []:
            if not isinstance(candidate, dict):
                continue
            # One subtitle can arrive twice: asking by hash and asking by
            # title are different questions with overlapping answers, and two
            # providers can serve the same file. A duplicate costs more than
            # it looks - `outlook` weighs *every* candidate against *every*
            # source when the picker opens, so one extra candidate is one
            # extra comparison per source, and this add-on exists to run on a
            # projector with a gigabyte of RAM.
            key = matcher.candidate_key(candidate)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            candidates.append(candidate)
    _remember_search(memo_key, candidates)
    return candidates


# One playback asks the providers the same questions several times over: once
# for the picker's outlook, again for what AI could translate from, again for
# the English rows, then again when the film actually starts, and again for
# timing evidence. Measured by tracing one film, four to seven rounds - and
# `outlook` is the only one of them that cached anything, under a key the
# others never match.
#
# So this is a short memo, not a cache. It exists to stop one playback asking
# twice, and nothing longer: a minute is far more than the seconds those
# rounds span, and far less than anything a viewer would notice as stale.
_SEARCHES = {}
_SEARCH_TTL = 60.0
_SEARCH_MAX = 8


def _search_memo_key(meta, languages, video_hash, split_languages):
    ids = meta.get("ids") or {}
    return (ids.get("tmdb"), ids.get("imdb"), meta.get("season"),
            meta.get("episode"), tuple(languages), bool(video_hash),
            bool(split_languages))


def _recall_search(key):
    held = _SEARCHES.get(key)
    if not held:
        return None
    when, found = held
    if time.time() - when > _SEARCH_TTL:
        _SEARCHES.pop(key, None)
        return None
    # Copies, because `matcher.rank` writes its score onto every candidate it
    # is given and the next caller must not inherit the last one's opinion.
    return [dict(candidate) for candidate in found]


def _remember_search(key, candidates):
    if not candidates:
        return          # a failure is not a result - see outlook.candidates
    if len(_SEARCHES) >= _SEARCH_MAX:
        _SEARCHES.clear()
    _SEARCHES[key] = (time.time(), [dict(c) for c in candidates])


def find_and_prepare(meta, languages, player=None, cancelled=None,
                     translation_generation=None, embedded=None):
    """Find, download, verify and store one subtitle. Returns (path, report).

    Strictly in three parts, each tried to the end before the next:
    Hebrew - a fitting one, then the best there is below the threshold; then
    AI - translated from what fits best, then from anything at all; then
    English - the file's own track, then a downloaded one. `embedded(code)`
    switches on the file's own track in that language and says whether it
    did; the report then carries `embedded` and the path is empty.
    """
    from . import consensus

    is_cancelled = cancelled or (lambda: False)
    report = {"translated": False, "synchronised": False, "reason": ""}
    downloads = _DownloadBudget(consensus.budget())
    wanted = languages[0]

    def keep(language, cues, variant=""):
        """Write the chosen subtitle, unless this playback is already over.

        The cancellation check used to happen only in `on_playback_started`,
        *after* this returned - so a superseded search still wrote its file
        and still ran `prune_cache()`, which deletes other titles' subtitles
        to stay under the cap. Pressing stop and starting something else could
        therefore evict the subtitle the new playback was about to reuse.
        """
        if is_cancelled():
            return ""
        return store(meta, language, cues, variant)


    get_hash = video_hash_later(meta)
    candidates = search_candidates(meta, languages, get_hash)
    if is_cancelled():
        return "", dict(report, reason="playback changed")
    # By here every provider that wanted it has already waited, so this is a
    # value rather than a wait.
    video_hash = get_hash()
    if not candidates:
        # Nothing in either configured language is not the same as nothing at
        # all. Before reporting a film as having no subtitles, ask the whole
        # catalogue and translate whatever it does have - which for anything
        # outside the mainstream is the difference between watching it and
        # not.
        path, report = _last_resort(meta, languages, report, player, video_hash,
                                    "no candidates", cancelled, downloads,
                                    generation=translation_generation)
        if path or is_cancelled():
            return path, report
        return _english(meta, languages, [], {}, report, downloads, 0,
                        embedded, keep)

    target = matcher.target_from(meta)
    threshold = settings.get_int("subs.threshold", 70)
    winners, _ranked = matcher.best(candidates, target, threshold,
                                    video_hash, languages)

    winner = winners.get(wanted)
    if winner and winner.get("accepted"):
        has_hash_reference = any(
            winners.get(language, {}).get("reason") == "hash"
            for language in languages[1:])
        if consensus.wanted(int(winner.get("score") or 0),
                            has_hash_reference):
            evidence = search_timing_evidence(meta, languages, video_hash)
            seen = {matcher.candidate_key(candidate) for candidate in candidates}
            candidates.extend(
                candidate for candidate in evidence
                if matcher.candidate_key(candidate) not in seen)
            winners, _ranked = matcher.best(candidates, target, threshold,
                                            video_hash, languages)
            winner = winners.get(wanted)
    if winner:
        kodi.log("best %s subtitle: %s %r"
                 % (wanted, matcher.explain(winner),
                    (winner.get("release") or "")[:60]))
    # Known once and used by every check below. From the player when there is
    # one, because that is this file's length rather than the title's rounded
    # runtime; 0 when nobody knows, which makes the coverage check stand aside.
    runtime = runtime_of(player, meta)
    if winner and winner.get("accepted"):
        winner, cues, report = _best_supported(
            winner, _ranked, wanted, languages, winners, report, threshold,
            downloads)
        if cues:
            cues, report = verify_and_sync(cues, winners, languages, report,
                                           downloads, runtime)
            if not cues and (report.get("hash_mismatch")
                             or report.get("short")):
                winner, cues, report = _verified_fallback(
                    _ranked, winner, wanted, winners, languages, report,
                    downloads, threshold, runtime)
            if cues:
                report["reason"] = (report.get("reason")
                                    or (winner or {}).get("reason", ""))
                return keep(wanted, cues), report

    if _subs_mode(meta) in ("native", "english"):
        # Chosen from the picker's Hebrew or English rows. The best Hebrew there is,
        # below the threshold if it has to be, and never a translation in its
        # place - otherwise a "native" row that fell back to AI would count
        # for the wrong side of the comparison.
        if winner:
            chosen, cues = _first_usable(_ranked, wanted, downloads)
            if cues:
                report["reason"] = "native row: best %s available" % wanted
                return keep(wanted, cues), report
        report["reason"] = "native row: no %s subtitle could be used" % wanted
        return "", report

    # Still Hebrew: the best there is, below the threshold if it has to be,
    # before any translation. A Hebrew subtitle somebody made for this title
    # comes ahead of one a model makes, and the viewer can switch it off.
    if winner:
        _chosen, cues, report = _first_verified(
            _ranked, wanted, downloads, winners, languages, report, runtime)
        if cues:
            report["reason"] = "below threshold, used anyway"
            return keep(wanted, cues), report

    # No Hebrew at all. Only now is it worth asking for what AI could
    # translate from: while any Hebrew exists those requests buy nothing.
    extra = translation_source_languages(meta, languages)
    if extra and not is_cancelled():
        more = search_candidates(meta, extra, video_hash)
        seen = {matcher.candidate_key(candidate) for candidate in candidates}
        candidates.extend(candidate for candidate in more
                          if matcher.candidate_key(candidate) not in seen)
        ai_languages = list(languages) + extra
        winners, _ranked = matcher.best(candidates, target, threshold,
                                        video_hash, ai_languages)
    else:
        ai_languages = languages

    path, report = translate_fallback(
        meta, winners, ai_languages, report, player, cancelled=cancelled,
        generation=translation_generation, downloads=downloads)
    if path:
        return path, report
    path, report = _last_resort(meta, languages, report, player, video_hash,
                                "nothing usable", cancelled, downloads,
                                generation=translation_generation)
    if path or is_cancelled():
        return path, report
    return _english(meta, languages, _ranked, winners, report, downloads,
                    runtime, embedded, keep)


def _english(meta, languages, ranked, winners, report, downloads, runtime,
             embedded=None, keep=None):
    """The last part: a subtitle in the next language down, as it is.

    The file's own track first, because it is in time by construction - for
    anime, the English a fansub release carries inside, which is how every
    other anime add-on shows subtitles at all. Then a downloaded one, which
    without an AI engine was never used: a film with a good English file on
    OpenSubtitles and no Hebrew played with nothing.
    """
    for code in languages[1:]:
        if embedded and embedded(code):
            report["embedded"] = code
            report["reason"] = "the file's own %s track" % code
            return "", report
        _chosen, cues, report = _first_verified(
            ranked, code, downloads, winners, languages, report, runtime)
        if cues:
            report["reason"] = "%s subtitle, nothing in %s" % (code, languages[0])
            report["language"] = code
            write = keep or (lambda language, found: store(meta, language, found))
            return write(code, cues), report
    return "", report


def _last_resort(meta, languages, report, player, video_hash, reason,
                 cancelled=None, downloads=None, generation=None):
    """Translate out of any language at all, having found nothing to show.

    Separate from `translate_fallback` because the two answer different
    questions. That one asks "is there a better route than this mediocre
    Hebrew match", and quite reasonably wants a source that matches well. This
    one is asked when there is no route at all, so it takes what it can get.
    """
    path = translate_now(meta, languages[0], player, video_hash=video_hash,
                         cancelled=cancelled, generation=generation,
                         downloads=downloads)
    if path:
        report["translated"] = True
        report["reason"] = "translated, nothing was available to download"
        return path, report
    report["reason"] = reason
    return "", report


# How long a provider will wait for the hash before asking without it. The
# whole search has a ten second deadline, so this has to leave room for the
# request that follows it.
# How long the three hash-keyed providers wait for it. Eight seconds rather
# than five, measured on the same file twice: 4588 ms once and 5317 ms the
# next time, so five sat exactly on the boundary and the second run missed it
# by 317 ms. Missing it is not one provider short - without the hash no
# candidate can come back marked "hash", so there is no timing reference for
# anything either, and the release name becomes the whole judgement. It costs
# nothing when the hash is quick, because the wait ends the moment it lands,
# and the other nine providers are being asked throughout.
HASH_WAIT = 8.0


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


def download_candidate(candidate, expect_language=None, outcome=None):
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
        # An exception in here is *our* fault, or one payload's - a shape that
        # changed, a key that moved. It is emphatically not "the service is
        # not serving", and recording it as that was how a one-line code bug
        # disabled a provider's entire catalogue: two of them and
        # `_DownloadBudget` writes the provider off for the whole operation,
        # reported to the viewer as "no subtitles".
        if outcome is not None:
            outcome["served"] = True
        return []
    # Whether the service handed over any bytes at all, which is a different
    # fault from an archive that arrived and would not parse: the first says
    # the provider is not serving, the second says this one file is bad.
    # `_DownloadBudget` writes a provider off only for the first.
    if outcome is not None:
        outcome["served"] = bool(data)
    if not data:
        return []
    try:
        cues = srt.parse(srt.decode(data, expect_language))
    except Exception:
        # `decode` assumes bytes and `parse` assumes text that behaves. Both
        # are handed whatever arrived over the network, and neither is inside
        # the guard above - a provider returning str rather than bytes raised
        # straight out of here.
        kodi.log_exception("could not read the subtitle %s sent"
                           % candidate.get("provider"))
        return []
    if not cues:
        return []
    cues = srt.clean(cues)
    if expect_language and cues and not srt.script_matches(cues, expect_language):
        kodi.log("%s offered a %s subtitle in a different script (%s)"
                 % (candidate.get("provider"), expect_language,
                    candidate.get("release", "")[:60]))
        return []
    return cues


# --------------------------------------------------------------------------
# verification and re-timing
# --------------------------------------------------------------------------


class _DownloadBudget(object):
    """One operation-wide, cached limit for all subtitle downloads.

    Two allowances, because a download that comes back empty is not the thing
    the limit exists to prevent. The limit is a memory decision - parse a few
    subtitles on a small device rather than fifty - and a fetch that returns
    nothing costs one request and no memory at all. Charging it anyway is how
    one sick provider took whole titles down with it: measured over 223 titles
    on 23 September 2026, `rest.opensubtitles.org` began refusing downloads
    part way through the run, and from that point three empty fetches spent
    the budget before anything else was asked. Pulp Fiction ended with no
    subtitle at all with 45 working Wizdom candidates behind the failures, and
    so did Taxi Driver, The Lion King, The Terminator and Harry Potter.

    A viewer meets this as subtitles that work all evening and then silently
    stop for every film, which is indistinguishable from the add-on breaking.
    """

    # Failures are correlated by provider, not spread evenly: when a service
    # stops serving, every one of its rows fails. So a provider is dropped for
    # the rest of the operation after this many empty fetches, and its
    # remaining rows are skipped for free rather than charged for. A flat
    # ceiling cannot do this - OpenSubtitles ranks eleven rows above Wizdom's
    # first on Pulp Fiction, so any ceiling low enough to be safe gives up
    # before reaching the provider that would have answered.
    PROVIDER_FAILURES = 2
    # A backstop across all providers, so a search returning 77 candidates can
    # never become 77 requests however they are spread.
    MAX_FAILURES = 10

    def __init__(self, limit):
        self.limit = max(0, int(limit))
        self.used = 0
        self.failed = 0
        self.failed_by_provider = {}
        self.cache = {}

    def _key(self, candidate, expect_language=None):
        return matcher.candidate_key(candidate, expect_language)

    def exhausted(self, candidate):
        """Has this candidate's provider already shown it is not serving?"""
        provider = candidate.get("provider") or ""
        return self.failed_by_provider.get(provider, 0) >= self.PROVIDER_FAILURES

    def fetch(self, candidate, expect_language=None):
        key = self._key(candidate, expect_language)
        if key in self.cache:
            return self.cache[key]
        if self.remaining() <= 0 or self.exhausted(candidate):
            return []
        outcome = {}
        cues = download_candidate(candidate, expect_language=expect_language,
                                  outcome=outcome)
        self.cache[key] = cues
        if cues:
            self.used += 1
            return cues
        self.failed += 1
        if outcome.get("served"):
            # The file arrived and was unusable. That is this upload's fault,
            # not the service's, and condemning the provider for it would
            # throw away the rest of its catalogue over two bad archives.
            return cues
        provider = candidate.get("provider") or ""
        self.failed_by_provider[provider] = (
            self.failed_by_provider.get(provider, 0) + 1)
        if self.failed_by_provider[provider] == self.PROVIDER_FAILURES:
            kodi.log("%s served nothing twice, skipping its remaining rows "
                     "for this subtitle search" % (provider or "?"))
        return cues

    def remaining(self):
        if self.failed >= self.MAX_FAILURES:
            return 0
        return max(0, self.limit - self.used)


# The matcher's base score for the right title is 40 and a name that states a
# different episode costs 100, so a score of 0 means exactly "this is another
# episode". Nothing may be applied on that evidence, however little else there
# is: measured, three season-zero specials got season one's first episode,
# because the "used anyway" fallback accepted any score down to zero and no
# provider has subtitles for a special.
MIN_USABLE_SCORE = 1


def _first_usable(candidates, wanted, downloads, minimum_score=MIN_USABLE_SCORE,
                  skip=None):
    """Try target candidates in rank order under one shared download budget."""
    for candidate in candidates:
        if candidate.get("language") != wanted:
            continue
        if candidate.get("score", 0) < minimum_score:
            continue
        if skip and matcher.candidate_key(candidate) in skip:
            continue
        cues = downloads.fetch(candidate, expect_language=wanted)
        if cues:
            return candidate, cues
        if downloads.remaining() <= 0:
            break
    return None, []


def _first_verified(candidates, wanted, downloads, winners, languages, report,
                    runtime, minimum_score=MIN_USABLE_SCORE):
    """The best candidate that also survives verification.

    `verify_and_sync` rejects what `_first_usable` hands it whenever a hash
    reference disproves the subtitle, or it ends too early to be the whole
    film - and its docstring has always said "the caller's fallback to the
    next candidate handles both". For the Hebrew and English steps that
    fallback did not exist, so one bad file ended the search.

    Measured over 223 titles: Harry Potter and the Chamber of Secrets had
    fourteen Hebrew subtitles, the best-ranked was a 710-cue CD1 half, and
    the film played with no subtitles at all while thirteen whole ones sat
    behind it. The download budget bounds the loop - only a fetch that
    succeeds costs a slot, and there are three.
    """
    tried = set()
    while True:
        chosen, cues = _first_usable(candidates, wanted, downloads,
                                     minimum_score, skip=tried)
        if not cues:
            return None, [], report
        cues, report = verify_and_sync(cues, winners, languages, report,
                                       downloads, runtime)
        if cues:
            return chosen, cues, report
        tried.add(matcher.candidate_key(chosen))


def _best_supported(winner, candidates, wanted, languages, winners, report,
                    threshold, downloads):
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

    has_reference = any(
        winners.get(language, {}).get("reason") == "hash"
        for language in languages[1:])
    if not consensus.wanted(int(winner.get("score") or 0), has_reference):
        chosen, cues = _first_usable(candidates, wanted, downloads, threshold)
        if chosen is not None and chosen is not winner:
            report["reason"] = "top candidate unusable, used next"
        return chosen or winner, cues, report

    picks = consensus.verification_candidates(candidates, wanted,
                                               downloads.remaining())
    if len(picks) < 2:
        chosen, cues = _first_usable(candidates, wanted, downloads, threshold)
        if chosen is not None and chosen is not winner:
            report["reason"] = "top candidate unusable, used next"
        return chosen or winner, cues, report

    fetched = []
    for candidate in picks:
        expected = wanted if candidate.get("language") == wanted else None
        cues = downloads.fetch(candidate, expect_language=expected)
        if expected and not cues:
            replacement, cues = _first_usable(
                candidates, wanted, downloads, threshold)
            if cues:
                fetched.append((replacement, cues))
            continue
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
        fitted, timing = _synchronise_safely(target[1], reference)
        if timing["applied"] or timing["reason"] == "already in sync":
            report["timing_evidence"] = "cross-language consensus"
            report["supported"] = evidence["supported"]
            report["confidence"] = timing["confidence"]
            report["synchronised"] = timing["applied"]
            return target[0], fitted, report
    if target is None:
        chosen, cues = _first_usable(candidates, wanted, downloads, threshold)
        if cues:
            report["reason"] = "top candidate unusable, used next"
            return chosen, cues, report

    same_language = [(candidate, cues) for candidate, cues in fetched
                     if candidate.get("language") == wanted]
    chosen, cues, outcome = consensus.choose(same_language)
    report["consensus"] = outcome.get("reason", "")
    report["supported"] = outcome.get("supported", 0)
    if chosen is None:
        # Consensus has no opinion, which is not a reason to have no subtitle.
        # This used to return None, and the caller's `if winner:` then skipped
        # the whole below-threshold Hebrew step - a usable candidate lost with
        # no log line and nothing to read afterwards. This is a tie-breaker
        # and never a gate, exactly as the docstring above says.
        kodi.log("consensus reached no verdict, keeping the top-scored %s"
                 % winner.get("provider"))
        return winner, cues, report
    if chosen is not winner:
        kodi.log("consensus preferred %s over the top-scored %s: %s"
                 % (chosen.get("provider"), winner.get("provider"),
                    outcome.get("reason")))
    return chosen, cues, report


# How far into the film a subtitle has to reach before it is believed to be
# for this cut: the last line's end over the runtime. It is the definition
# tools/survey_subtitles.py already reports on, and until now it existed only
# there - the playback path had no runtime check at all, so a subtitle that
# stopped sixty percent of the way through (a shorter version, one part of a
# split file, or simply incomplete) was shown whenever its filename scored
# well enough. Conservative on purpose: credits and a silent ending rarely
# take a film's last line below ninety percent.
MIN_RUNTIME_COVERAGE = 0.6


def runtime_of(player, meta):
    """The length of what is playing, in seconds, or 0 when it is not known.

    The player's own figure first, because it is this file, cut and all,
    where TMDB's is a rounded number for the title. Unknown is a real answer -
    a live stream, or a player that has not learned the length yet - and it
    makes the coverage check stand aside rather than guess.
    """
    try:
        total = float(player.getTotalTime() or 0) if player is not None else 0.0
    except Exception:
        total = 0.0
    if total > 0:
        return total
    try:
        return float((meta or {}).get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def covers_runtime(cues, runtime):
    """Does this subtitle reach far enough into the film to be for it?"""
    if not cues or runtime <= 0:
        return True
    return cues[-1].end / float(runtime) >= MIN_RUNTIME_COVERAGE


def _synchronise_safely(cues, reference):
    """`sync.synchronise`, which correlates attacker-supplied numbers.

    The input is a file somebody uploaded, and this is the one place where its
    contents decide how much work the device does. The timeline is clamped in
    `sync.activity_mask` so the pathological case is cheap now, but the
    correlator is still the most data-driven code here, and `consensus.agree`
    has always wrapped its own `sync.fit` call for exactly this reason while
    the two main callers did not. A refusal to align is a normal outcome - the
    subtitle is simply shown at its original timing - so there is nothing to
    gain from letting an exception end the playback's subtitle search.
    """
    try:
        return sync.synchronise(cues, reference)
    except Exception:
        kodi.log_exception("could not align this subtitle, leaving its timing")
        return cues, {"applied": False, "offset": 0.0, "scale": 1.0,
                      "confidence": 0.0, "reason": "alignment failed"}


def verify_and_sync(cues, winners, languages, report, downloads, runtime=0):
    """Check the chosen subtitle against a trusted reference, and re-time it.

    The reference is a hash-matched subtitle in another language. Its timings
    are known to be right for this exact file, so it doubles as proof that the
    chosen subtitle belongs to this episode at all: an unrelated file scores
    near zero however it is shifted, and is then rejected rather than shown.

    Then, reference or not, the result has to reach far enough into the film.
    After re-timing rather than before, because a framerate correction moves
    the last line by four percent and it is the corrected subtitle that will
    be shown. A rejection returns no cues with a flag set, exactly as a
    disproved hash does, so the caller's fallback to the next candidate
    handles both.
    """
    reference = reference_cues(winners, languages, downloads)
    if reference:
        fitted, result = _synchronise_safely(cues, reference)
        report["confidence"] = result["confidence"]
        if result["applied"]:
            report["synchronised"] = True
            kodi.log("subtitle re-timed by %.2fs, scale %.5f, confidence %.2f"
                     % (result["offset"], result["scale"],
                        result["confidence"]))
        elif result["confidence"] < sync.MIN_CONFIDENCE:
            report["hash_mismatch"] = True
            report["reason"] = "hash reference disproved subtitle"
            kodi.log("subtitle rejected against exact-file hash reference: "
                     "%.2f" % result["confidence"])
            return [], report
        else:
            kodi.log("subtitle timing left alone: %s" % result["reason"])
        cues = fitted

    if not covers_runtime(cues, runtime):
        reached = cues[-1].end / float(runtime)
        report["short"] = True
        report["reason"] = "ends %d%% of the way through" % int(reached * 100)
        kodi.log("subtitle rejected: its last line is at %ds of a %ds film"
                 % (cues[-1].end, runtime))
        return [], report
    return cues, report


def _verified_fallback(candidates, rejected, wanted, winners, languages,
                       report, downloads, minimum_score, runtime=0):
    """Try another target after the first was disproved.

    Disproved by exact-file evidence, or by stopping too early for the film -
    both return no cues with a flag set, and both deserve the same answer:
    the next candidate that clears the threshold, checked the same way.
    """
    because = ("a higher-ranked candidate was too short for this film"
               if report.get("short")
               else "hash reference rejected higher-ranked candidate")
    rejected_key = downloads._key(rejected, wanted)
    for candidate in candidates:
        if (candidate.get("language") != wanted
                or candidate.get("score", 0) < minimum_score
                or downloads._key(candidate, wanted) == rejected_key):
            continue
        cues = downloads.fetch(candidate, expect_language=wanted)
        if not cues:
            if downloads.remaining() <= 0:
                break
            continue
        cues, report = verify_and_sync(cues, winners, languages, report,
                                       downloads, runtime)
        if cues:
            report["reason"] = because
            return candidate, cues, report
        if downloads.remaining() <= 0:
            break
    return None, [], report


def reference_cues(winners, languages, downloads, skip=None):
    """A subtitle whose timings we know are correct for this file.

    `skip` is the candidate being judged, so a file can never be its own
    ruler - which the translation path can otherwise ask for, because there
    the subtitle under test is in one of these same other languages.
    """
    for language in languages[1:]:
        candidate = winners.get(language)
        if candidate is skip:
            continue
        if candidate and candidate.get("reason") == "hash":
            cues = downloads.fetch(candidate)
            if cues:
                kodi.log("using the %s hash match as a timing reference" % language)
                return cues
    return []


def hash_reference(candidates, downloads, skip=None):
    """A hash-matched subtitle from `candidates`, for use as a timing ruler."""
    for candidate in candidates or []:
        if candidate is skip or candidate.get("reason") != "hash":
            continue
        cues = downloads.fetch(candidate)
        if cues:
            return cues
    return []


def retimed_for_translation(cues, reference, language=""):
    """Put the source subtitle in time *before* it is translated.

    Both translation paths used to say "timings come from the source subtitle
    and are never touched", and that was the whole of it: a Polish subtitle
    found by release name became a well-gendered Hebrew subtitle that was out
    by however much the Polish one was out, with nothing anywhere to notice.
    The Hebrew path has re-timed against a hash-matched reference since the
    beginning; the AI path simply never asked for one.

    Shifting is all it does. `verify_and_sync` may reject a subtitle the
    reference disproves, and that is right when there is another Hebrew
    candidate behind it - here the source has already been chosen and the
    budget spent, so refusing would leave the viewer with nothing rather than
    with something a second out. A poor fit is left exactly where it was,
    which is what the reference not existing already does.
    """
    if not reference or not cues:
        return cues
    fitted, result = _synchronise_safely(cues, reference)
    if result["applied"]:
        kodi.log("the %s subtitle was re-timed by %.2fs before translating "
                 "(scale %.5f, confidence %.2f)"
                 % (language or "source", result["offset"], result["scale"],
                    result["confidence"]))
        return fitted
    kodi.log("the %s subtitle was left where it was before translating: %s"
             % (language or "source", result["reason"]))
    return cues


def translate_fallback(meta, winners, languages, report, player=None,
                       cancelled=None, generation=None, downloads=None):
    """Translate the best other-language match into the wanted language.

    The source is re-timed first where there is a hash-matched reference to
    re-time it against, because a translation inherits its source's timings
    and nothing downstream looks at them again. Without a reference they are
    kept as they are, so this is exactly as well synchronised as the file it
    came from whenever there is no ruler.

    Which source to translate from is not simply the best match. Hebrew marks
    the speaker's gender on verbs and adjectives, and a language that already
    marks it carries that through for free, so a Spanish or Arabic subtitle can
    beat a slightly better-matching English one.
    """
    from .ai import context as translation_context
    from .ai import coordinator, translator
    from . import consensus

    if downloads is None:
        downloads = _DownloadBudget(consensus.budget())
    if not translator.available():
        return "", report

    if generation is None:
        generation = coordinator.begin()

    def stopped():
        return ((cancelled is not None and cancelled())
                or not coordinator.current(generation))

    if stopped():
        return "", report
    ranked = translation_context.rank_translation_sources(winners, languages)
    source = next(((lang, cand) for lang, cand in ranked
                   if (cand.get("score") or 0) >= 40), None)
    if not source:
        return "", report

    language, candidate = source
    if stopped():
        return "", report
    cues = downloads.fetch(candidate)
    if not cues:
        return "", report
    cues = retimed_for_translation(
        cues, reference_cues(winners, languages, downloads, skip=candidate),
        language)

    translated = _translate_progressively(cues, meta, languages[0], player,
                                          cancelled=stopped,
                                          generation=generation)
    if stopped() or not translated:
        return "", report

    def apply_final():
        path = store(meta, languages[0], translated)
        if path and player is not None:
            player.setSubtitles(path)
            player.showSubtitles(True)
        return path

    committed, path = coordinator.commit(generation, apply_final)
    if not committed or not path:
        return "", report
    report["translated"] = True
    report["source_language"] = language
    report["applied"] = player is not None
    return path, report


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
        candidates = search_candidates(meta, languages, video_hash,
                                       split_languages=True)
    if not candidates:
        return []
    ranked = matcher.rank(candidates, matcher.target_from(meta), video_hash,
                          languages)
    return translation_context.rank_translation_candidates(ranked, target)


def translate_now(meta, target, player=None, candidates=None, video_hash=None,
                  cancelled=None, generation=None, downloads=None):
    """Translate the best subtitle we can find into `target`, and return its path.

    This is the viewer saying "what I have is not good enough" - or that there
    is nothing at all - so it ignores the acceptance threshold entirely and
    does not care whether a subtitle already exists in the target language.
    Its whole job is to produce one.

    Timings come from the source subtitle, re-timed against a hash-matched
    reference where one exists and otherwise untouched, so the result fits
    as well as the file it was translated from. Each finished
    chunk goes on screen as it arrives, because a feature film is minutes of
    translation and nobody should watch a progress bar for it.

    A source that turns out to be undownloadable does not end the attempt; the
    next best language is tried, which matters most in exactly the case this
    exists for - the obscure title where the one English subtitle listed is a
    dead link.
    """
    from .ai import coordinator, translator
    from . import consensus

    if downloads is None:
        downloads = _DownloadBudget(consensus.budget())
    if not translator.available():
        kodi.log("AI translation was asked for but no engine is configured")
        return ""

    if generation is None:
        generation = coordinator.begin()

    def stopped():
        return ((cancelled is not None and cancelled())
                or not coordinator.current(generation))

    if stopped():
        return ""
    if video_hash is None:
        video_hash = video_hash_for(meta)
    sources = translation_sources(meta, target, video_hash, candidates)
    if stopped() or not sources:
        kodi.log("found nothing at all to translate into %s" % target)
        return ""

    for language, candidate in sources:
        if stopped():
            return ""
        cues = downloads.fetch(candidate)
        if not cues:
            continue
        kodi.log("translating the %s subtitle %r into %s"
                 % (language, (candidate.get("release") or "")[:60], target))
        cues = retimed_for_translation(
            cues, hash_reference(candidates, downloads, skip=candidate),
            language)
        translated = _translate_progressively(cues, meta, target, player,
                                              variant=VARIANT_AI,
                                              cancelled=stopped,
                                              generation=generation)
        if translated:
            def apply_final():
                path = store(meta, target, translated, variant=VARIANT_AI)
                if path and player is not None:
                    player.setSubtitles(path)
                    player.showSubtitles(True)
                return path

            committed, path = coordinator.commit(generation, apply_final)
            return path if committed else ""
        # A source that downloaded successfully has already consumed the one
        # operation-wide model budget. Only dead downloads fall through; a
        # model failure must not reset the budget on another candidate.
        return ""
    kodi.log("every translation source failed to download")
    return ""


def _translate_progressively(cues, meta, language, player, variant="",
                             cancelled=None, generation=None):
    """Translate, showing each finished chunk as it arrives.

    A feature-length film is several minutes of translation. Waiting for all of
    it before showing anything means staring at a progress bar; showing the
    first chunk immediately means the viewer starts watching while the rest is
    still being written.

    Kodi caches a subtitle file by path, so re-writing the same name changes
    nothing on screen. Alternating between two names forces it to re-read.
    """
    import xbmcgui

    from .ai import coordinator, translator

    generation = generation if generation is not None else coordinator.begin()

    def stopped():
        return ((cancelled is not None and cancelled())
                or not coordinator.current(generation))

    progress = xbmcgui.DialogProgressBG()
    progress.create("Pinky", kodi.localize(32335))
    # Everything from here is inside the try whose finally closes that bar.
    # `_partial_slots` was outside it, and it is not as safe as it looks: it
    # runs `int(meta["season"])` on a value that has round-tripped through
    # JSON from a window property, and makes a directory in the profile. Either
    # one raising left a progress bar on the screen with nothing left alive to
    # close it - the viewer's only way out was restarting Kodi.
    slots = []
    state = {"slot": 0, "shown": 0}

    def on_progress(done, total, partial=None):
        if stopped():
            return
        progress.update(int(done * 100 / max(1, total)),
                        message=kodi.localize(32335))
        if player is None or partial is None or done <= state["shown"]:
            return
        if not slots:
            return                  # nowhere to write one yet
        path = slots[state["slot"] % len(slots)]

        def apply_partial():
            # Both checks are before the write, not around it. `srt.write`
            # used to sit between them, so a job superseded while it was
            # writing still put a file on disk for a playback that had already
            # been replaced - and on slow eMMC that write is not instant.
            if cancelled is not None and cancelled():
                return False
            if not coordinator.current(generation):
                return False
            srt.write(path, partial)
            player.setSubtitles(path)
            player.showSubtitles(True)
            state["slot"] += 1
            state["shown"] = done
            return True

        try:
            coordinator.commit(generation, apply_partial)
        except Exception:
            kodi.log_exception("could not show a partial translation")

    try:
        slots[:] = _partial_slots(meta, language, variant, generation)
        kwargs = {"on_progress": on_progress, "meta": meta,
                  "cancelled": stopped}
        return translator.translate(cues, language, **kwargs)
    except translator.TranslationError:
        kodi.log_exception("AI translation failed")
        if player is not None and state["shown"]:
            def hide_rejected():
                if cancelled is not None and cancelled():
                    return False
                player.showSubtitles(False)
                return True

            try:
                coordinator.commit(generation, hide_rejected)
            except Exception:
                kodi.log_exception("could not hide rejected partial translation")
        return []
    finally:
        progress.close()
        _clean_partials(slots)


def _partial_slots(meta, language, variant="", generation=0):
    """Two job-specific names to alternate between while translating."""
    base = name_for(meta, language, variant)[:-4]
    directory = subtitle_dir()
    return [os.path.join(directory, "%s.job%d.part%s.srt"
                         % (base, generation, suffix))
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


def _release_fingerprint(meta):
    """Stable selected-file identity; never includes a temporary stream URL."""
    source = (meta or {}).get("source") or {}
    name = source.get("file_name") or source.get("release") or ""
    size = source.get("file_size") or ""
    media_hash = source.get("video_hash") or source.get("torrent_hash") or ""
    identity = "%s|%s|%s" % (media_hash, name, size)
    if identity == "||":
        return ""
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()[:10]


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

    fingerprint = _release_fingerprint(meta)
    release_mark = ".r%s" % fingerprint if fingerprint else ""
    mark = "%s%s" % (release_mark, ".%s" % variant if variant else "")
    if meta.get("type") == "episode":
        return "%s.s%02de%02d%s.%s.srt" % (key, int(meta.get("season") or 0),
                                           int(meta.get("episode") or 0),
                                           mark, language)
    return "%s%s.%s.srt" % (key, mark, language)


def cached_subtitle(meta, language, variant=""):
    """A validated subtitle prepared earlier for this selected media file."""
    path = os.path.join(subtitle_dir(), name_for(meta, language, variant))
    if not os.path.isfile(path):
        return ""
    try:
        if not srt.read(path):
            return ""
        # Modification time is the cache's LRU signal; a successful hit counts
        # as use, otherwise frequently watched subtitles are pruned as old.
        os.utime(path, None)
        return path
    except (OSError, IOError):
        return ""


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
