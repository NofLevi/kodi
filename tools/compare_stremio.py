# -*- coding: utf-8 -*-
"""Katan's automatic subtitles against what Stremio would show, on one file.

    python tools/compare_stremio.py --count 30     needs the test profile's debrid key
    python tools/compare_stremio.py --report

Both sides get what they get in real life. Stremio hands a subtitle add-on the
IMDb address, the file's OpenSubtitles hash, its size and its name; Katan runs
its own automatic path, `auto.find_and_prepare`, as the player does. The
Stremio side is the free setup a Hebrew viewer would have: the official
OpenSubtitles v3 add-on and Hebrew Subtitle Bridge. Two others were left out -
hebrew-subs-multi no longer answers, and Heb Subs Premium is paid.

The ruler is a subtitle matched to this exact file by hash, in any language:
its timings are right by construction. Stremio auto-selects the first subtitle
in the viewer's language, official add-on first, so that is the one judged
for it.

Read the numbers knowing this: where a hash-matched reference exists, Katan
re-times against it by design, so its "in sync" there partly measures that it
uses the reference at all - which is the point of doing so, and something no
Stremio add-on does.
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys
import threading
import time

try:
    from urllib.parse import quote, urlencode
except ImportError:                                   # pragma: no cover
    from urllib import quote, urlencode

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tests", "stubs"))
sys.path.insert(0, os.path.join(ROOT, "plugin.video.katan", "resources", "lib"))

import survey_sources as base  # noqa: E402
import survey_subtitles as measured  # noqa: E402

OUT = os.path.join(ROOT, ".survey", "stremio-compare.jsonl")

ADDONS = (("opensubtitles", "https://opensubtitles-v3.strem.io"),
          ("bridge", "https://stremiosubs.fusiontv.co"))
HEBREW = ("heb", "he", "hebrew")
IN_SYNC = 0.5          # seconds; later than this and a viewer notices

_out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
_lock = threading.Lock()


def say(text=""):
    _out.write(text + "\n")
    _out.flush()


# --------------------------------------------------------------------------
# one title
# --------------------------------------------------------------------------


def examine(entry):
    from katan import play, settings
    from katan.subs import auto, srt

    started = time.time()
    record = dict(entry)
    record.update(error="", streamed=False, hashed=False, release="",
                  reference="", katan={}, stremio={}, stremio_pick="")
    try:
        meta = play.build_meta({
            "type": entry["kind"], "tmdb": entry["tmdb"],
            "season": entry.get("season", 0),
            "episode": entry.get("episode", 0),
        })
    except Exception as error:
        record["error"] = "meta: %s" % str(error)[:90]
        return record
    record["resolved_title"] = meta.get("title", "")
    record["runtime"] = int(meta.get("duration") or 0)

    try:
        chosen, url = _open(_sources(meta))
    except Exception as error:
        record["error"] = "sources: %s" % str(error)[:90]
        return record
    if not url:
        record["error"] = "no cached stream"
        return record
    record["streamed"] = True
    meta["source"] = play.source_record(chosen)
    meta["stream_url"] = url
    record["release"] = (chosen.get("title") or "")[:120]
    file_name = chosen.get("file_name") or chosen.get("title") or ""

    video_hash = auto.video_hash_for(meta)
    record["hashed"] = bool(video_hash)
    languages = settings.get_list("subs.languages") or ["he", "en"]

    # Stremio first, so the reference can fall back on its hash matches.
    offered = _ask_stremio(meta, video_hash, meta.get("stream_size") or 0,
                           file_name)
    reference, record["reference"] = _reference(meta, languages, video_hash,
                                                offered)

    # Katan, the way the player runs it.
    begun = time.time()
    try:
        path, report = auto.find_and_prepare(meta, languages)
        cues = srt.read(path) if path else []
    except Exception as error:
        record["error"] = "katan: %s" % str(error)[:90]
        path, report, cues = "", {}, []
    record["katan"] = dict(
        _judge(cues, reference, record["runtime"]),
        reason=report.get("reason", ""),
        synchronised=bool(report.get("synchronised")),
        translated=bool(report.get("translated")),
        seconds=round(time.time() - begun, 1))

    for name, _url in ADDONS:
        entries = offered.get(name)
        hebrew = [e for e in entries or []
                  if str(e.get("lang") or "").lower() in HEBREW]
        info = {"answered": entries is not None, "total": len(entries or []),
                "hebrew": len(hebrew)}
        if hebrew:
            info.update(_judge(_download(hebrew[0].get("url")), reference,
                               record["runtime"]))
            info["hash_match"] = hebrew[0].get("m") == "h"
        record["stremio"][name] = info
        if not record["stremio_pick"] and info.get("found"):
            record["stremio_pick"] = name

    record["ms"] = int((time.time() - started) * 1000)
    return record


def _sources(meta):
    """What a viewer would be offered, ranked - as survey_subtitles does."""
    from katan.sources import aggregator, model, scoring

    providers = aggregator._enabled_providers(meta)
    raw = aggregator._run_providers(providers, meta, quiet=True)
    merged = model.dedupe(raw)
    also = aggregator._anime_address(meta)
    if also:
        by_id = [(n, m) for n, m in providers if not getattr(m, "BY_NAME", False)]
        merged = model.dedupe(raw + aggregator._run_providers(by_id, also,
                                                              quiet=True))
    sources, _rejected = scoring.rank_all(merged, meta,
                                          aggregator._runtime_hours(meta))
    return sources


def _open(sources):
    """The first cached source that resolves, and its URL. Cached only: a
    survey must not queue downloads onto somebody's account."""
    from katan import play

    for source in sources[:6]:
        if not source.get("cached"):
            continue
        try:
            url = play._resolve(source)
        except Exception:
            continue
        if url:
            return source, url
    return None, ""


def _ask_stremio(meta, video_hash, size, file_name):
    """{add-on: its subtitle list, or None when it did not answer}."""
    from katan import http

    imdb = (meta.get("ids") or {}).get("imdb") or ""
    if not imdb:
        # Both add-ons are addressed by IMDb id only ("idPrefixes": ["tt"]),
        # so a title without one gets nothing from them - as in Stremio.
        return {name: [] for name, _url in ADDONS}
    if meta.get("type") == "movie":
        kind, address = "movie", imdb
    else:
        kind = "series"
        address = "%s:%d:%d" % (imdb, int(meta.get("season") or 0),
                                int(meta.get("episode") or 0))
    extra = {"filename": file_name}
    if video_hash:
        extra["videoHash"] = video_hash
    if size:
        extra["videoSize"] = size
    found = {}
    for name, root in ADDONS:
        url = "%s/subtitles/%s/%s/%s.json" % (root, kind, quote(address, ":"),
                                             urlencode(extra))
        payload = http.get_json(url, timeout=(5, 40), default=None)
        found[name] = None if payload is None else (payload.get("subtitles") or [])
    return found


def _reference(meta, languages, video_hash, offered):
    """(cues, description) of a subtitle matched to this file by hash."""
    from katan.subs import auto, matcher

    if not video_hash:
        return [], ""
    try:
        candidates = auto.search_candidates(meta, languages, video_hash)
        ranked = matcher.rank(candidates, matcher.target_from(meta),
                              video_hash, languages)
        match = measured._hash_matched(ranked, video_hash)
        cues = measured._cues(match) if match else None
        if cues:
            return cues, "katan %s %s" % (match.get("provider", ""),
                                          match.get("language", ""))
    except Exception:
        pass
    for entry in offered.get("opensubtitles") or []:
        if entry.get("m") == "h":
            cues = _download(entry.get("url"))
            if cues:
                return cues, "stremio opensubtitles %s" % entry.get("lang", "")
    return [], ""


def _download(url):
    from katan import http
    from katan.subs import srt

    if not url:
        return []
    try:
        response = http.get(url, timeout=(5, 40))
        if response is None or response.status_code != 200:
            return []
        return srt.parse(srt.decode(response.content, "he"))
    except Exception:
        return []


def _judge(cues, reference, runtime):
    """Is this subtitle the right one, and is it in time?"""
    from katan.subs import sync

    out = {"found": bool(cues)}
    if not cues:
        return out
    out["cues"] = len(cues)
    if runtime:
        out["reach"] = round(cues[-1].end / float(runtime), 2)
    if not reference:
        out["measured"] = False
        return out
    offset, scale, confidence = sync.fit(cues, reference)
    right = confidence >= sync.MIN_CONFIDENCE
    out.update(measured=True, confidence=round(confidence, 3),
               offset=round(offset, 2), scale=scale, right=right,
               in_sync=bool(right and abs(offset) <= IN_SYNC
                            and abs(scale - 1.0) < 0.001))
    return out


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------


def run(count, workers, path):
    sample = base.build_sample(count)
    done = base.already_done(path)
    todo = [e for e in sample
            if (e["kind"], e["tmdb"], e.get("season", 0),
                e.get("episode", 0)) not in done]
    say("%d titles, %d already done, %d to go" % (len(sample),
                                                   len(sample) - len(todo),
                                                   len(todo)))
    handle = io.open(path, "a", encoding="utf-8", newline="")
    counter = {"n": 0}
    queue = list(reversed(todo))

    def worker():
        while True:
            try:
                entry = queue.pop()
            except IndexError:
                return
            try:
                record = examine(entry)
            except Exception as error:
                record = dict(entry, error="crashed: %s" % str(error)[:90])
            with _lock:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                counter["n"] += 1
                say("  %3d/%-3d %-30s katan %-10s stremio %-10s %s"
                    % (counter["n"], len(todo),
                       (record.get("resolved_title") or "?")[:30],
                       _verdict(record.get("katan")),
                       _verdict(_pick(record)),
                       (record.get("error") or "")[:30]))

    threads = [threading.Thread(target=worker) for _ in range(max(1, workers))]
    for thread in threads:
        thread.daemon = True
        thread.start()
    try:
        for thread in threads:
            while thread.is_alive():
                thread.join(0.5)
    except KeyboardInterrupt:
        say("\nstopping - what is written so far is kept")
    finally:
        handle.close()
    return 0


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------


def _pick(record):
    return (record.get("stremio") or {}).get(record.get("stremio_pick") or "") or {}


def _verdict(side):
    side = side or {}
    if not side.get("found"):
        return "none"
    if not side.get("measured"):
        return "unmeasured"
    if not side.get("right"):
        return "wrong"
    if side.get("in_sync"):
        return "in-sync"
    return "off %.1fs" % abs(side.get("offset") or 0)


def _count(rows, test):
    return sum(1 for r in rows if test(r))


def report(path):
    if not os.path.isfile(path):
        raise SystemExit("nothing at %s yet" % os.path.relpath(path, ROOT))
    rows = [json.loads(line) for line in io.open(path, encoding="utf-8")
            if line.strip()]
    streamed = [r for r in rows if r.get("streamed")]
    say("%d titles, %d with a cached stream to test on" % (len(rows),
                                                           len(streamed)))
    if not streamed:
        return 0

    def line(label, n, of):
        say("  %-34s %3d of %-3d (%d%%)" % (label, n, of, 100 * n // max(1, of)))

    say("")
    say("HEBREW OFFERED AT ALL")
    line("Katan", _count(streamed, lambda r: r["katan"].get("found")
                         and not r["katan"].get("translated")), len(streamed))
    line("Katan, counting AI translation", _count(
        streamed, lambda r: r["katan"].get("found")), len(streamed))
    line("Stremio (either add-on)", _count(streamed, lambda r: _pick(r)),
         len(streamed))
    line("  OpenSubtitles v3 alone", _count(
        streamed, lambda r: r["stremio"].get("opensubtitles", {}).get("found")),
        len(streamed))
    line("  Hebrew Subtitle Bridge alone", _count(
        streamed, lambda r: r["stremio"].get("bridge", {}).get("found")),
        len(streamed))

    ruled = [r for r in streamed if r.get("reference")]
    say("")
    say("FIT - against a subtitle matched to the file by hash (%d titles)"
        % len(ruled))
    if ruled:
        for label, side in (("Katan", lambda r: r["katan"]),
                            ("Stremio's auto-pick", _pick),
                            ("  OpenSubtitles v3 first Hebrew",
                             lambda r: r["stremio"].get("opensubtitles", {})),
                            ("  Bridge first Hebrew",
                             lambda r: r["stremio"].get("bridge", {}))):
            got = [side(r) for r in ruled if side(r).get("found")]
            right = [s for s in got if s.get("right")]
            synced = [s for s in got if s.get("in_sync")]
            say("  %-34s had %-3d right file %-3d in sync %-3d of %d"
                % (label, len(got), len(right), len(synced), len(ruled)))
        say("  in sync = the right file, within %.1fs, no frame-rate stretch."
            % IN_SYNC)

    say("")
    say("TITLE BY TITLE")
    say("  %-32s %-12s %-12s %s" % ("title", "katan", "stremio", "reference"))
    for r in streamed:
        say("  %-32s %-12s %-12s %s" % ((r.get("resolved_title") or "?")[:32],
                                        _verdict(r["katan"]),
                                        _verdict(_pick(r)),
                                        r.get("reference") or "-"))
    down = [r for r in streamed
            if not (r.get("stremio") or {}).get("bridge", {}).get("answered", True)]
    if down:
        say("")
        say("  the bridge did not answer for %d titles" % len(down))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--out", default=OUT)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if args.report:
        return report(args.out)
    base.boot(use_debrid=True)
    return run(args.count, args.workers, args.out)


if __name__ == "__main__":
    sys.exit(main())
