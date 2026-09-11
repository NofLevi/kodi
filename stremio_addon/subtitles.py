# -*- coding: utf-8 -*-
"""Hebrew subtitles for whatever Stremio is playing.

Stremio asks every subtitle add-on at once and waits only briefly, while a
search takes seconds and a translation up to a minute. So the answer is
always immediate - one entry pointing at /sub/<key>.srt - and the work runs
behind it. When Stremio loads that file, it waits for the work to finish.

The work, in order:
1. Katan's own Hebrew search (Wizdom, OpenSubtitles, BSPlayer...), the file's
   hash included; a good match is used, re-timed when a hash-matched
   subtitle gives the timing.
2. Otherwise an English subtitle - for anime from AnimeTosho (this exact
   file) or Kitsunekko, else Katan's own English match - translated with
   Gemini.
3. Otherwise the best Hebrew found, however weak.

Every result is kept, so a second play costs nothing, and after a
translation the next episode is prepared too.
"""
import hashlib
import io
import logging
import os
import re
import threading
import time

import runtime

SUB_DIR = os.path.join(runtime.DATA, "subs")
KEEP_FILES = 3000
WAIT_SECONDS = 100
GOOD_SCORE = 70
ENGLISH_SCORE = 40
JOB_MEMORY = 3600

log = logging.getLogger("katan.subtitles")

_jobs = {}
_jobs_lock = threading.Lock()
# One translation at a time: Gemini is paced process-wide anyway, and two
# half-finished files help nobody.
_translating = threading.Lock()


class Job(object):
    def __init__(self, key):
        self.key = key
        self.done = threading.Event()
        self.partial = None
        self.started = time.time()
        self.source = ""


# --------------------------------------------------------------------------
# what Stremio sends
# --------------------------------------------------------------------------


def parse_address(item_id):
    """{"scheme", "id", "season", "episode"} for tt... and kitsu:... ids."""
    parts = (item_id or "").split(":")
    if parts[0].startswith("tt") and parts[0][2:].isdigit():
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            return {"scheme": "imdb", "id": parts[0],
                    "season": int(parts[1]), "episode": int(parts[2])}
        if len(parts) == 1:
            return {"scheme": "imdb", "id": parts[0], "season": 0, "episode": 0}
    if parts[0] == "kitsu" and len(parts) >= 2 and parts[1].isdigit():
        episode = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else 0
        return {"scheme": "kitsu", "id": parts[1], "season": 1 if episode else 0,
                "episode": episode}
    return None


def key_for(item_id, filename, video_hash=""):
    identity = "%s|%s" % (item_id, (filename or video_hash or "").lower())
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]


def path_for(key):
    return os.path.join(SUB_DIR, "%s.he.srt" % key)


def handle(item_id, extra, base_url):
    """The subtitle list for Stremio: one entry, prepared in the background."""
    address = parse_address(item_id)
    if not address:
        return []
    extra = extra or {}
    filename = extra.get("filename") or ""
    video_hash = extra.get("videoHash") or ""
    size = _int(extra.get("videoSize"))
    key = key_for(item_id, filename, video_hash)
    if not os.path.isfile(path_for(key)):
        start(key, item_id, address, filename, size, video_hash, ahead=True)
    return [{"id": "katan-he", "url": "%s/sub/%s.srt" % (base_url, key),
             "lang": "heb"}]


def serve(key):
    """The subtitle file's bytes, waiting for its job; None when there is none."""
    path = path_for(key)
    if not os.path.isfile(path):
        with _jobs_lock:
            job = _jobs.get(key)
        if job is None:
            return None
        job.done.wait(WAIT_SECONDS)
        if not os.path.isfile(path):
            partial = job.partial
            if partial:
                from katan.subs import srt
                return srt.dump(partial).encode("utf-8")
            return None
    with io.open(path, "rb") as handle_:
        return handle_.read()


# --------------------------------------------------------------------------
# the work
# --------------------------------------------------------------------------


def start(key, item_id, address, filename, size, video_hash, ahead=False):
    with _jobs_lock:
        _forget_old_jobs()
        if key in _jobs:
            return _jobs[key]
        job = _jobs[key] = Job(key)
    worker = threading.Thread(
        target=_run, args=(job, item_id, address, filename, size, video_hash,
                           ahead),
        name="katan-sub-%s" % key)
    worker.daemon = True
    worker.start()
    return job


def _forget_old_jobs():
    now = time.time()
    for key in [k for k, j in _jobs.items()
                if j.done.is_set() and now - j.started > JOB_MEMORY]:
        del _jobs[key]


def _run(job, item_id, address, filename, size, video_hash, ahead):
    try:
        meta = build_meta(address)
        meta["source"] = {"file_name": filename, "release": filename,
                          "file_size": size, "video_hash": video_hash}
        meta["stream_size"] = size
        cues, how = prepare(meta, filename, video_hash, job)
        if cues:
            save(job.key, cues)
            log.info("subtitle %s ready: %s", item_id, how)
            if ahead and how.startswith("translated") and \
                    runtime.config().get("translate_ahead"):
                _prepare_next(item_id, address, meta, filename)
        else:
            log.info("no subtitle for %s", item_id)
    except Exception:
        log.exception("preparing a subtitle for %s failed", item_id)
    finally:
        job.done.set()


def build_meta(address):
    """Katan's metadata for what Stremio is playing."""
    if address["scheme"] == "imdb":
        from katan import play
        from katan.meta import tmdb

        found = tmdb.find_by_imdb(address["id"]) or {}
        tmdb_id = (found.get("ids") or {}).get("tmdb")
        if tmdb_id:
            if address["episode"]:
                return play.build_meta({"type": "episode", "tmdb": tmdb_id,
                                        "season": address["season"],
                                        "episode": address["episode"],
                                        "imdb": address["id"]})
            return play.build_meta({"type": "movie", "tmdb": tmdb_id,
                                    "imdb": address["id"]})
        return {"type": "episode" if address["episode"] else "movie",
                "ids": {"imdb": address["id"]}, "title": "",
                "season": address["season"], "episode": address["episode"]}

    from katan.meta import kitsu

    item = kitsu.details(address["id"]) or {}
    return {
        "type": "episode" if address["episode"] else "movie",
        "ids": {"kitsu": address["id"]},
        "title": item.get("title") or "",
        "original_title": item.get("original_title") or "",
        "search_title": item.get("title") or "",
        "year": item.get("year") or 0,
        "season": address["season"],
        "episode": address["episode"],
        "absolute": address["episode"],
        "extra": {"anime": True},
    }


def prepare(meta, filename, video_hash, job):
    """(cues, how it was made) - see the module docstring for the order."""
    from katan.subs import auto, matcher, sync
    from katan.subs.ai import translator

    languages = ["he", "en"]
    try:
        candidates = auto.search_candidates(meta, languages, video_hash)
    except Exception:
        log.exception("subtitle search failed")
        candidates = []
    ranked = matcher.rank(candidates, matcher.target_from(meta), video_hash,
                          languages)
    reference = _reference(ranked, video_hash)

    weak = None
    for candidate in ranked:
        if candidate.get("language") != "he":
            continue
        cues = auto.download_candidate(candidate, "he")
        if not cues:
            continue
        if int(candidate.get("score") or 0) >= GOOD_SCORE:
            if reference:
                cues, _report = sync.synchronise(cues, reference)
            return cues, "hebrew from %s" % candidate.get("provider")
        weak = weak or cues
        break

    if translator.available():
        english, source = english_source(meta, filename, ranked)
        if english:
            if reference and not source.startswith("AnimeTosho"):
                english, _report = sync.synchronise(english, reference)
            job.source = source
            translated = _translate(english, meta, job)
            if translated:
                return translated, "translated from %s" % source

    if weak:
        return weak, "weak hebrew match"
    return [], ""


def english_source(meta, filename, ranked):
    """(cues, label) of the English to translate from."""
    import english
    from katan.subs import auto

    anime = (meta.get("extra") or {}).get("anime")
    if anime:
        cues, label = english.from_animetosho(filename)
        if cues:
            return cues, label
        titles = [meta.get("search_title"), meta.get("original_title"),
                  meta.get("title")]
        cues, label = english.from_kitsunekko(
            titles, meta.get("season"), meta.get("episode"),
            meta.get("absolute"), filename)
        if cues:
            return cues, label
    for candidate in ranked:
        if candidate.get("language") != "en":
            continue
        if int(candidate.get("score") or 0) < ENGLISH_SCORE:
            break
        cues = auto.download_candidate(candidate, "en")
        if cues:
            return cues, "%s (English)" % candidate.get("provider")
    return [], ""


def _reference(ranked, video_hash):
    """A subtitle matched to this file by hash: its timings are right."""
    from katan.subs import auto

    for candidate in ranked:
        if candidate.get("hash_match") or (
                video_hash and candidate.get("moviehash") == video_hash):
            cues = auto.download_candidate(candidate)
            if cues:
                return cues
    return []


def _translate(cues, meta, job):
    from katan.subs.ai import translator

    def progress(done, total, merged=None):
        if merged:
            job.partial = merged

    with _translating:
        try:
            return translator.translate(cues, "he", on_progress=progress,
                                        meta=meta)
        except translator.TranslationError as error:
            log.warning("translation failed: %s", error)
            return []


def save(key, cues):
    from katan.subs import srt

    if not os.path.isdir(SUB_DIR):
        os.makedirs(SUB_DIR)
    path = path_for(key)
    temporary = path + ".tmp"
    with io.open(temporary, "wb") as handle_:
        handle_.write(srt.dump(cues).encode("utf-8"))
    os.replace(temporary, path)
    _prune()


def _prune():
    try:
        names = [os.path.join(SUB_DIR, n) for n in os.listdir(SUB_DIR)
                 if n.endswith(".srt")]
    except OSError:
        return
    if len(names) <= KEEP_FILES:
        return
    names.sort(key=os.path.getmtime)
    for name in names[:len(names) - KEEP_FILES]:
        try:
            os.remove(name)
        except OSError:
            pass


# --------------------------------------------------------------------------
# the next episode
# --------------------------------------------------------------------------


def _prepare_next(item_id, address, meta, filename):
    """Translate the next episode now, for the same release, if it can be named."""
    if not address.get("episode"):
        return
    numbers = [n for n in (meta.get("episode"), meta.get("absolute")) if n]
    next_name = next_filename(filename, numbers)
    if not next_name:
        return
    following = dict(address, episode=address["episode"] + 1)
    parts = item_id.split(":")
    parts[-1] = str(following["episode"])
    next_id = ":".join(parts)
    key = key_for(next_id, next_name)
    if not os.path.isfile(path_for(key)):
        log.info("preparing the next episode ahead: %s", next_name)
        start(key, next_id, following, next_name, 0, "", ahead=False)


_EPISODE_PATTERNS = (
    re.compile(r"(?i)(s\d{1,2}e)(\d{1,4})"),
    re.compile(r"(?i)(\bep?\.?\s?)(\d{1,4})(?!\d)"),
    re.compile(r"(\s-\s)(\d{1,4})(?!\d)"),
    re.compile(r"(\[)(\d{1,4})(\])"),
    re.compile(r"([._ ])(\d{2,4})(?=[._ ])"),
)


def next_filename(filename, numbers):
    """The same release's file name for the next episode, or "".

    Only the number that is this episode's changes - EP31 becomes EP32,
    " - 09 " becomes " - 10 " - with its padding kept.
    """
    if not filename or not numbers:
        return ""
    wanted = set(int(n) for n in numbers)
    for pattern in _EPISODE_PATTERNS:
        for found in pattern.finditer(filename):
            digits = found.group(2)
            if int(digits) not in wanted:
                continue
            bumped = str(int(digits) + 1).zfill(len(digits))
            start_, end = found.span(2)
            return filename[:start_] + bumped + filename[end:]
    return ""


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
