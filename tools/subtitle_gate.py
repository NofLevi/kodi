# -*- coding: utf-8 -*-
"""Before/after gate for subtitle fit: films, series and anime.

    python tools/subtitle_gate.py            the working tree against HEAD
    python tools/subtitle_gate.py v0.0.5     against any other ref
    python tools/subtitle_gate.py --refresh  ask the services again

The fit a row shows in the picker is `matcher.rate` of the best subtitle
against that release. This scores the *same* releases and the *same*
subtitle candidates with the ref's matcher and the working tree's, per
engine, so a difference is the scorer and nothing else. The releases and
candidates are fetched once, with the add-on's own search, and kept under
`.survey/subtitle_gate/` - rerunning costs no requests.

Per title and per list (Hebrew, English, a language AI would translate
from) it prints the best fit and the average of the ten best releases,
which is roughly what the picker's rows show. A change to one engine should
leave the other two identical.
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
REF = ARGS[0] if ARGS else "HEAD"
REFRESH = "--refresh" in sys.argv
CACHE = os.path.join(ROOT, ".survey", "subtitle_gate")
sys.path.insert(0, os.path.join(ROOT, "plugin.video.pinky/resources/lib"))
sys.path.insert(0, os.path.join(ROOT, "tests/stubs"))
import xbmcaddon  # noqa: E402

_PROFILE = os.path.join(tempfile.gettempdir(), "pinky-subtitle-gate")
os.makedirs(_PROFILE, exist_ok=True)
xbmcaddon.reset(_PROFILE, os.path.join(ROOT, "plugin.video.pinky"))

from pinky import play, settings  # noqa: E402
from pinky.sources import scoring  # noqa: E402
from pinky.subs import auto  # noqa: E402
import pinky.subs  # noqa: E402,F401
from pinky.subs import matcher as new  # noqa: E402

settings.set("sources.cached_only", "false")

_source = subprocess.check_output(
    ["git", "show", REF + ":plugin.video.pinky/resources/lib/pinky/subs/matcher.py"],
    cwd=ROOT)
_spec = importlib.util.spec_from_loader("pinky.subs._matcher_ref", loader=None)
old = importlib.util.module_from_spec(_spec)
old.__package__ = "pinky.subs"
exec(compile(_source, "matcher_ref.py", "exec"), old.__dict__)

LANGUAGES = ["he", "en", "ar", "es", "fr", "ru", "pl"]
TITLES = {  # engine: [(type, tmdb, season, episode)]
    "FILMS": [("movie", 603, 0, 0), ("movie", 496243, 0, 0),
              ("movie", 872585, 0, 0), ("movie", 550, 0, 0),
              ("movie", 27205, 0, 0), ("movie", 693134, 0, 0)],
    "SERIES": [("episode", 1396, 1, 1), ("episode", 1399, 1, 1),
               ("episode", 66732, 1, 1), ("episode", 93405, 1, 2),
               ("episode", 71446, 1, 1), ("episode", 95396, 1, 1)],
    "ANIME": [("episode", 31910, 3, 55), ("episode", 31654, 1, 2),
              ("episode", 1429, 1, 1), ("episode", 85937, 1, 1),
              ("episode", 95479, 1, 1), ("episode", 120089, 1, 1),
              ("episode", 209867, 1, 1), ("episode", 13916, 1, 1),
              # Later episodes of long runs, where subtitle names stop
              # carrying a group, a source or even a show name.
              ("episode", 31654, 1, 20), ("episode", 46260, 1, 100),
              ("episode", 37854, 1, 45), ("episode", 30984, 1, 20),
              ("episode", 209867, 1, 20), ("episode", 13916, 1, 20),
              ("episode", 31910, 5, 100)],
}


def _get(url):
    """GET JSON, waiting out a 429 rather than recording an empty answer."""
    for attempt in range(4):
        request = urllib.request.Request(url, headers={"User-Agent": "pinky-gate/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 3:
                raise
            time.sleep(20 * (attempt + 1))


def _releases(meta):
    imdb = meta["ids"].get("imdb")
    names = []
    for base in ("https://torrentio.strem.fun", "https://torrentsdb.com"):
        if meta["type"] == "movie":
            url = "%s/stream/movie/%s.json" % (base, imdb)
        else:
            url = "%s/stream/series/%s:%d:%d.json" % (
                base, imdb, meta["season"], meta["episode"])
        try:
            streams = _get(url).get("streams") or []
        except Exception:
            continue
        for stream in streams:
            hints = stream.get("behaviorHints") or {}
            names.append(hints.get("filename")
                         or (stream.get("title") or "").split("\n")[0])
    return names


def _gather(kind, tmdb, season, episode):
    path = os.path.join(CACHE, "%s-%s-%d-%d.json" % (kind, tmdb, season, episode))
    if os.path.exists(path) and not REFRESH:
        with io.open(path, encoding="utf-8") as handle:
            record = json.load(handle)
        if record["releases"]:
            return record
    meta = play.build_meta({"type": kind, "tmdb": str(tmdb),
                            "season": season, "episode": episode})
    releases = _releases(meta) if meta["ids"].get("imdb") else []
    candidates = auto.search_candidates(meta, LANGUAGES)
    record = {"meta": {k: v for k, v in meta.items() if k != "item"},
              "releases": releases,
              "candidates": [{"language": c.get("language"),
                              "release": c.get("release") or c.get("name") or "",
                              "provider": c.get("provider"),
                              "downloads": c.get("downloads"),
                              "sync_percent": c.get("sync_percent")}
                             for c in candidates]}
    os.makedirs(CACHE, exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False)
    return record


def _kept(record):
    sources = [{"title": name, "quality": "1080p", "source": "web",
                "codec": "h264", "hdr": [], "audio": "unknown",
                "size": 2 * 1024 ** 3, "seeders": 20, "languages": [],
                "hash": "%040d" % (i + 1), "cached": False,
                "provider": "torrentio"}
               for i, name in enumerate(record["releases"])]
    ranked, _ = scoring.rank(sources, record["meta"], limit=0)
    return [source["title"] for source in ranked]


def _fits(module, record, releases, group):
    if group == "he":
        wanted = ("he",)
    elif group == "en":
        wanted = ("en",)
    else:
        wanted = ("ar", "es", "fr", "ru", "pl")
    candidates = [c for c in record["candidates"] if c["language"] in wanted]
    if not candidates or not releases:
        return []
    per_release = []
    for name in releases:
        target = module.target_from(record["meta"], {"file_name": name})
        per_release.append(max(module.rate(c, target)[0] for c in candidates))
    return sorted(per_release, reverse=True)


def _show(fits):
    if not fits:
        return "   -     -  "
    top = fits[:10]
    return "%3d %5.1f " % (fits[0], sum(top) / float(len(top)))


totals = {}
for engine, titles in TITLES.items():
    print("=== %s ===   best/avg10 per list: Hebrew | English | AI-source" % engine)
    for kind, tmdb, season, episode in titles:
        record = _gather(kind, tmdb, season, episode)
        releases = _kept(record)
        label = (record["meta"].get("english_title") or record["meta"].get("search_title")
                 or record["meta"].get("original_title") or str(tmdb))
        if kind == "episode":
            label = "%s %dx%02d" % (label, season, episode)
        row = []
        for group in ("he", "en", "ai"):
            before = _fits(old, record, releases, group)
            after = _fits(new, record, releases, group)
            row.append("%s->%s" % (_show(before), _show(after)))
            bucket = totals.setdefault((engine, group), [0, 0, 0])
            if before and after:
                bucket[0] += 1
                bucket[1] += sum(before[:10]) / float(len(before[:10]))
                bucket[2] += sum(after[:10]) / float(len(after[:10]))
            flag = "" if not before or not after or \
                sum(after[:10]) >= sum(before[:10]) else " REGRESSION"
            row[-1] += flag
        print("%-34s %4d rel  %s" % (label[:34], len(releases), " | ".join(row)))
print()
for (engine, group), (n, before, after) in sorted(totals.items()):
    if n:
        print("%-6s %-3s  avg10 %5.1f -> %5.1f   over %d titles"
              % (engine, group, before / n, after / n, n))
