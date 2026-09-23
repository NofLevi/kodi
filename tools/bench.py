# -*- coding: utf-8 -*-
"""Time the hot paths, so a refactor can be judged rather than believed.

Every case here is something that runs while somebody is waiting: opening a
row, searching for a source, working out which subtitle fits. The numbers are
the best of several runs, because the worst of a run on a desktop is usually
Windows deciding to do something else.

    python tools/bench.py                 run and print
    python tools/bench.py --save before   record for a later comparison
    python tools/bench.py --against before  run and diff against it
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys
import time
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDON = os.path.join(ROOT, "plugin.video.pinky")
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tests", "stubs"))
sys.path.insert(0, os.path.join(ADDON, "resources", "lib"))

_out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def say(text):
    print(text, file=_out)
    _out.flush()


def boot():
    import tempfile
    import xbmcaddon  # noqa: F401
    from pinky import kodi, settings
    kodi.profile_path = lambda: tempfile.mkdtemp(prefix="katan-bench-")
    settings.set("subs.languages", "he,en")
    settings.set("sources.max_resolution", "2160p")
    settings.set("sources.cached_only", "false")
    return settings


# --------------------------------------------------------------------------
# the material: release names of the shape the providers really return
# --------------------------------------------------------------------------

NAMES = [
    "The.Matrix.1999.1080p.BluRay.x264-AMIABLE",
    "The.Matrix.1999.2160p.UHD.BluRay.x265.HDR.Atmos-TERMINAL",
    "Silo.S01E01.Freedom.Day.1080p.ATVP.WEB-DL.DDP5.1.H.264-NTb.mkv",
    "[HorribleSubs] Kateikyoushi Hitman Reborn! (125-203) [720p] (Batch)",
    "[Feibanyama] BLEACH Thousand Year Blood War S01E46 [IQIYI WebRip 2160p]",
    "Dune.Part.Two.2024.1080p.WEB-DL.H264.AAC-InMemoryOfEVO.mkv",
    "Les evades (1994) - 1080p FR EN x264 ac3 mHDgz.mkv",
    "Show.S02.COMPLETE.1080p.WEB-DL-GRP",
    "[Yonkou]_One_Piece_539_[HD][01891224].mkv",
    "Matrix.1999.FRENCH.BRRip.XviD.AC3-Torrent911",
]


def sources(count=240):
    """A source list the size a real search returns."""
    from pinky.sources import model
    out = []
    for index in range(count):
        name = NAMES[index % len(NAMES)]
        out.append(model.from_release_name(
            "%s.%d" % (name, index // len(NAMES)),
            provider="torrentio", info_hash="%040x" % index,
            size=(1 + index % 9) * 1024 ** 3, seeders=index % 400))
    return out


def subtitle_candidates(count=32):
    return [{"provider": "wizdom", "language": "he",
             "release": NAMES[index % len(NAMES)],
             "download": str(index)} for index in range(count)]


def cues(count=1400):
    from pinky.subs import srt
    return [srt.Cue(i + 1, i * 4.0, i * 4.0 + 2.5, "line %d" % i)
            for i in range(count)]


# --------------------------------------------------------------------------
# the cases
# --------------------------------------------------------------------------


def case_parse():
    from pinky.utils import release
    names = [n + str(i) for i in range(40) for n in NAMES]

    def run():
        for name in names:
            release.parse(name)
    return "release.parse, 400 names", run


def case_dedupe():
    from pinky.sources import model
    raw = sources(240) + sources(240)

    def run():
        model.dedupe(raw)
    return "model.dedupe, 480 sources", run


def case_rank():
    from pinky.sources import scoring
    found = sources(240)

    def run():
        scoring.rank_all(list(found), {"type": "movie", "ids": {}}, 2.0)
    return "scoring.rank_all, 240 sources", run


def case_outlook():
    """The one that scales badly: every source against every candidate."""
    from pinky.subs import outlook
    found = sources(240)
    candidates = subtitle_candidates(32)
    meta = {"type": "movie", "ids": {"tmdb": 603}, "title": "The Matrix",
            "source": {"release": NAMES[0]}}

    def run():
        outlook.annotate(meta, [dict(s) for s in found], candidates)
    return "outlook.annotate, 240x32", run


def case_matcher():
    from pinky.subs import matcher
    candidates = subtitle_candidates(32) * 8
    target = matcher.target_from({"type": "movie",
                                  "source": {"release": NAMES[0]}})

    def run():
        matcher.rank([dict(c) for c in candidates], target, "", ["he"])
    return "matcher.rank, 256 candidates", run


def case_srt():
    from pinky.subs import srt
    text = srt.dump(cues(1400))

    def run():
        srt.clean(srt.parse(text))
    return "srt parse+clean, 1400 cues", run


def case_sync():
    from pinky.subs import sync
    reference = cues(1400)
    shifted = [type(c)(c.index, c.start + 2.5, c.end + 2.5, c.text)
               for c in reference]

    def run():
        sync.synchronise(shifted, reference)
    return "sync.synchronise, 1400 cues", run


def case_sync_on_kodis_python():
    """The same alignment, with the popcount Kodi 21 actually uses.

    `int.bit_count()` is Python 3.10 and Kodi 21 ships 3.8, so every number
    measured with the builtin is a number no device will ever see. This forces
    the fallback, which is the honest figure - and it is the one that was
    thirty times worse than the other line in this table.
    """
    from pinky.subs import sync
    from pinky.subs import srt

    reference = cues(1400)
    # A different rhythm, so nothing lines up: this is the case a refusal
    # takes, and the one that never trips the early break.
    unrelated = [srt.Cue(i + 1, i * 3.1 + 5.0, i * 3.1 + 6.7, "other %d" % i)
                 for i in range(1400)]
    table = sync._BITS_IN_BYTE

    def fallback(value):
        return sum(value.to_bytes((value.bit_length() + 7) // 8, "big")
                   .translate(table))

    def run():
        real = sync._popcount
        sync._popcount = fallback
        try:
            sync.fit(unrelated, reference)
        finally:
            sync._popcount = real
    return "sync.fit, unrelated, Python 3.8 path", run


def case_cache():
    from pinky import cache
    payload = [{"ids": {"tmdb": i}, "title": "Item %d" % i,
                "art": {"poster": "http://x/%d.jpg" % i}} for i in range(20)]

    def run():
        for i in range(40):
            cache.set("bench|%d" % i, payload, 600)
            cache.get("bench|%d" % i)
    return "cache set+get, 40 rows", run


CASES = [case_parse, case_dedupe, case_rank, case_outlook, case_matcher,
         case_srt, case_sync, case_sync_on_kodis_python, case_cache]


def best_of(run, rounds=5):
    """Fastest of several runs, and the peak memory one run allocates.

    Time is the best of the rounds because the worst is usually Windows doing
    something else. Peak memory is measured once, under `tracemalloc`, because
    it is deterministic and tracing makes everything slower - so it must not
    be inside the timed rounds.
    """
    timings = []
    for _ in range(rounds):
        started = time.time()
        run()
        timings.append((time.time() - started) * 1000)

    tracemalloc.start()
    try:
        run()
        peak = tracemalloc.get_traced_memory()[1] / 1024.0
    finally:
        tracemalloc.stop()
    return min(timings), peak


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save")
    parser.add_argument("--against")
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()

    boot()
    results = {}
    say("")
    peaks = {}
    say("%-38s %10s %12s" % ("", "ms", "peak KB"))
    say("-" * 62)
    for factory in CASES:
        label, run = factory()
        run()                                   # warm imports and caches
        milliseconds, peak = best_of(run, args.rounds)
        results[label] = round(milliseconds, 3)
        peaks[label] = round(peak, 1)
        say("%-38s %10.2f %12.1f" % (label, results[label], peaks[label]))
    total = round(sum(results.values()), 3)
    say("-" * 62)
    say("%-38s %10.2f %12.1f" % ("total", total, max(peaks.values() or [0])))

    if args.save:
        path = os.path.join(ROOT, ".bench-%s.json" % args.save)
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(results, indent=2, sort_keys=True))
        say("")
        say("saved to %s" % path)

    if args.against:
        path = os.path.join(ROOT, ".bench-%s.json" % args.against)
        if not os.path.isfile(path):
            say("no baseline at %s" % path)
            return
        with io.open(path, encoding="utf-8") as handle:
            before = json.load(handle)
        say("")
        say("%-34s %10s %10s %9s" % ("against " + args.against, "before",
                                     "after", "change"))
        say("-" * 66)
        for label in sorted(results):
            was = before.get(label)
            now = results[label]
            if was is None:
                say("%-34s %10s %10.2f %9s" % (label, "-", now, "new"))
                continue
            change = ((now - was) / was * 100.0) if was else 0.0
            say("%-34s %10.2f %10.2f %8.0f%%" % (label, was, now, change))
        was_total = sum(before.values())
        say("-" * 66)
        say("%-34s %10.2f %10.2f %8.0f%%"
            % ("total", was_total, total,
               (total - was_total) / was_total * 100.0 if was_total else 0))


if __name__ == "__main__":
    main()
