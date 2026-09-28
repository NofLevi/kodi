# -*- coding: utf-8 -*-
"""Before/after gate for the source engines: films, series and anime.

    python tools/engine_gate.py            compare the working tree with HEAD
    python tools/engine_gate.py v0.0.5     compare it with any other ref

The same live releases are scored by the ref's scoring.py and the working
tree's, with metadata built the way a Hebrew interface builds it, so any
difference is the code and not the day's Torrentio answer. A change to one
engine should leave the other two identical; a row marked REGRESSION kept
fewer releases than before.
"""
import importlib.util, json, os, subprocess, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
sys.path.insert(0, os.path.join(ROOT, "plugin.video.pinky/resources/lib"))
sys.path.insert(0, os.path.join(ROOT, "tests/stubs"))
import pinky.sources  # noqa: E402
from pinky.sources import scoring as new  # noqa: E402

head_src = subprocess.check_output(
    ["git", "show", REF + ":plugin.video.pinky/resources/lib/pinky/sources/scoring.py"],
    cwd=ROOT)
spec = importlib.util.spec_from_loader("pinky.sources._scoring_head", loader=None)
old = importlib.util.module_from_spec(spec)
old.__package__ = "pinky.sources"
exec(compile(head_src, "scoring_head.py", "exec"), old.__dict__)

from pinky.meta import tmdb as _tmdb  # noqa: E402
KEY = _tmdb.BUNDLED_KEY
MOVIES = [603, 27205, 496243, 872585, 693134, 155, 550, 680]
SERIES = [1399, 1396, 66732, 95396, 94997, 100088, 93405, 71446, 88040, 70523, 1668, 60059]
ANIME = [31910, 1429, 37854, 85937, 65930, 13916, 95479, 120089, 46260, 30984, 65942, 94605]

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "pinky-gate/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

def tmdb(path, **p):
    p["api_key"] = KEY
    return get("https://api.themoviedb.org/3%s?%s" % (path, urllib.parse.urlencode(p)))

def releases(kind, imdb, season=1, episode=1):
    out = []
    for base in ("https://torrentio.strem.fun", "https://torrentsdb.com"):
        url = ("%s/stream/movie/%s.json" % (base, imdb) if kind == "movie" else
               "%s/stream/series/%s:%d:%d.json" % (base, imdb, season, episode))
        try:
            data = get(url)
        except Exception:
            continue
        for i, s in enumerate(data.get("streams") or []):
            bh = s.get("behaviorHints") or {}
            name = bh.get("filename") or (s.get("title") or "").split("\n")[0]
            out.append({"title": name, "quality": "1080p", "source": "web",
                        "codec": "h264", "hdr": [], "audio": "unknown",
                        "size": 2 * 1024 ** 3, "seeders": 20, "languages": [],
                        "hash": "%040d" % (len(out) + 1), "cached": False,
                        "provider": "torrentio"})
    return out

def rank(module, sources, meta):
    ranked, rejected = module.rank([dict(s) for s in sources], meta, limit=0)
    return len(ranked), rejected.get("another series of the same name", 0)

def build(kind, tv, anime):
    path = "/movie/%s" % tv if kind == "movie" else "/tv/%s" % tv
    he = tmdb(path, language="he-IL")
    ext = tmdb(path + "/external_ids")
    name_key = "title" if kind == "movie" else "name"
    orig_key = "original_title" if kind == "movie" else "original_name"
    meta = {"type": "movie" if kind == "movie" else "episode",
            "title": he.get(name_key) or "", "original_title": he.get(orig_key) or "",
            "original_language": he.get("original_language") or "",
            "year": int((he.get("release_date") or he.get("first_air_date") or "0")[:4] or 0),
            "season": 1, "episode": 1, "ids": {"imdb": ext.get("imdb_id")},
            "extra": {"anime": anime}}
    if kind != "movie":
        meta["show_title"] = meta["title"]
    before = dict(meta)
    if anime:  # what HEAD's build_meta already added for anime
        en = tmdb(path, language="en-US")
        alt = tmdb(path + "/alternative_titles").get("results") or []
        before["search_title"] = en.get(name_key) or ""
        before["aliases"] = [a["title"] for a in alt if a.get("iso_3166_1") == "JP"
                             and "romaji" in (a.get("type") or "").lower()][:4]
    after = dict(before)
    if kind != "movie" and not anime:  # what the new build_meta adds
        tr = tmdb(path + "/translations").get("translations") or []
        pairs = [(t.get("iso_639_1"), ((t.get("data") or {}).get("name") or "").strip()) for t in tr]
        after["translated_titles"] = [n for _l, n in pairs if n]
        en = [n for l, n in pairs if l == "en" and n]
        after["english_title"] = en[0] if en else (
            after["original_title"] if after["original_language"] == "en" else "")
    return before, after, ext.get("imdb_id"), (tmdb(path, language="en-US").get(name_key) or "")

totals = {}
for label, kind, ids, anime in (("MOVIES", "movie", MOVIES, False),
                                ("SERIES", "tv", SERIES, False),
                                ("ANIME", "tv", ANIME, True)):
    print("=== %s ===" % label)
    t = totals.setdefault(label, [0, 0, 0, 0, 0])
    for tv in ids:
        before, after, imdb, en_name = build(kind, tv, anime)
        if not imdb:
            continue
        src = releases(kind, imdb)
        kb, sb = rank(old, src, before)
        ka, sa = rank(new, src, after)
        t[0] += len(src); t[1] += kb; t[2] += ka; t[3] += sb; t[4] += sa
        flag = "" if ka >= kb else "   <-- REGRESSION"
        print("%-30s n=%3d  kept %3d -> %3d   'another series' %3d -> %3d%s"
              % (en_name[:30], len(src), kb, ka, sb, sa, flag))
        time.sleep(0.3)
print()
for label, (n, kb, ka, sb, sa) in totals.items():
    print("%-7s releases=%4d  kept %4d -> %4d   'another series' %4d -> %4d"
          % (label, n, kb, ka, sb, sa))
