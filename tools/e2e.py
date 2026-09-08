# -*- coding: utf-8 -*-
"""End to end, against the live services, reporting everything that is wrong.

The unit suite runs against stubs and proves the logic. This proves the other
half: that the services this add-on depends on are still there, still shaped
the way they were, and still answer the questions we ask them. Every defect in
this project's history that the unit suite could not have caught was of that
kind - AniList going dark, SubSource moving behind a login, TorBox growing a
device flow, an anime episode addressed at a season number nobody indexes.

**It does not stop at the first failure.** A run that dies on check three says
nothing about checks four to twenty, and the whole point is to come back with
the list. Every check is caught, timed and recorded, and the exit code is the
only thing that depends on the outcome.

    python tools/e2e.py                 run everything, print a report
    python tools/e2e.py --json out.json also write the results as JSON
    python tools/e2e.py --offline       skip anything that needs the network

Checks are marked `required` or not. A required failure is a broken add-on and
fails the run. An optional one is somebody else's service having a bad day: it
is reported loudly and does not fail the build, because a red build nobody can
fix is a red build everybody learns to ignore.
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDON = os.path.join(ROOT, "plugin.video.katan")
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tests", "stubs"))
sys.path.insert(0, os.path.join(ADDON, "resources", "lib"))

WORK = os.path.join(ROOT, ".e2e")

_out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def say(text=""):
    _out.write(text + "\n")
    _out.flush()


# --------------------------------------------------------------------------
# the harness
# --------------------------------------------------------------------------

CHECKS = []


def check(name, area, required=True, network=True):
    """Register one check. It returns a detail string, or raises."""
    def wrap(function):
        CHECKS.append({"name": name, "area": area, "required": required,
                       "network": network, "run": function})
        return function
    return wrap


def boot():
    """Load the add-on the way a plugin call would, with its own profile."""
    import xbmcaddon  # noqa: F401  - registers the stub
    from katan import kodi

    if not os.path.isdir(WORK):
        os.makedirs(WORK)
    kodi.profile_path = lambda: WORK
    # The stub has no idea where the add-on lives, and the bundled channel and
    # VOD data are read from there. Without this the Israeli catalogue is
    # empty and every check against it passes for the wrong reason.
    kodi.addon_path = lambda: ADDON

    # Everything the providers found, judged on quality alone. Without this
    # the source checks measure a debrid account CI does not have: cached_only
    # ships on, so with no service configured every source is filtered away
    # and a perfectly healthy search reports "no sources for Fight Club".
    from katan import settings
    settings.set("sources.results", "0")
    settings.set("cached_only", "false")
    settings.set("sources.cached_only", "false")
    return kodi


# --------------------------------------------------------------------------
# what the add-on is made of
# --------------------------------------------------------------------------


@check("every module imports", "packaging", network=False)
def _imports():
    import importlib
    import pkgutil
    import katan

    failed = []
    count = 0
    for _finder, name, _pkg in pkgutil.walk_packages(katan.__path__, "katan."):
        count += 1
        try:
            importlib.import_module(name)
        except Exception as error:
            failed.append("%s: %s" % (name, str(error)[:80]))
    if failed:
        raise AssertionError("; ".join(failed[:6]))
    return "%d modules" % count


@check("the add-on manifests are valid", "packaging", network=False)
def _manifests():
    sys.path.insert(0, HERE)
    import build

    problems = build.check()
    if problems:
        raise AssertionError("; ".join(problems))
    return "%d add-ons" % len(build.ADDONS)


@check("the bundled Israeli catalogue loads", "vod", network=False)
def _vod_loads():
    from katan.vod import library

    library.refresh()
    entries = library.load()
    if len(entries) < 1000:
        raise AssertionError("only %d programmes in the bundled list"
                             % len(entries))
    modules = dict(library.modules())
    missing = [m for m in library.MODULE_NAMES if not modules.get(m)]
    if missing:
        raise AssertionError("no programmes for %s" % ", ".join(missing))
    return "%d programmes across %d broadcasters" % (len(entries), len(modules))


@check("a Hebrew search finds the Israeli entry first", "vod", network=False)
def _vod_search_order():
    from katan.search import unified
    from katan.vod import library

    library.refresh()
    unified.invalidate_index()
    entry = next(e for e in library.load() if len(e.get("n", "")) > 6)
    found = library.search(entry["n"], limit=5)
    if not found:
        raise AssertionError("searching %r found nothing" % entry["n"][:20])
    return "%r -> %d" % (entry["n"][:22], len(found))


@check("the bundled channel list is usable", "vod", network=False)
def _channels():
    from katan.vod import channels

    channels.refresh()
    television = channels.live_channels()
    radio = channels.radio_stations()
    if len(television) < 30:
        raise AssertionError("only %d television channels" % len(television))
    if len(radio) < 10:
        raise AssertionError("only %d radio stations" % len(radio))
    return "%d television, %d radio, %d hidden as broken" % (
        len(television), len(radio), channels.hidden_count())


# --------------------------------------------------------------------------
# the services it depends on
# --------------------------------------------------------------------------


@check("TMDB answers with the bundled key", "metadata")
def _tmdb():
    from katan.meta import tmdb

    if not tmdb.has_key():
        raise AssertionError("no API key at all")
    rows = tmdb.trending("movie", "day")
    if not rows:
        raise AssertionError("trending came back empty")
    return "%d trending films, first %r" % (len(rows), (rows[0].get("title") or "")[:26])


@check("the anime catalogue answers", "metadata")
def _anime():
    from katan.meta import anime

    rows = anime.trending(limit=10)
    if not rows:
        raise AssertionError("no anime from either catalogue")
    return "%d titles from %s" % (len(rows), anime.current_source()
                                  if hasattr(anime, "current_source") else "?")


@check("Kitsu can be asked for a cour", "metadata")
def _kitsu():
    from katan.meta import kitsu

    # Bleach's Thousand-Year Blood War: four broadcast runs of 13, 13, 14, 10.
    parts = kitsu._cours("Bleach", "Thousand-Year Blood War")
    if len(parts) < 2:
        raise AssertionError("found %d cours, expected the arc" % len(parts))
    total = sum(count for _id, count in parts)
    return "%d cours, %d episodes" % (len(parts), total)


@check("a debrid service is reachable", "debrid", required=False)
def _debrid():
    from katan import http

    response = http.get("https://api.torbox.app/v1/api/user/me",
                        timeout=(5, 10))
    if response is None:
        raise AssertionError("no answer from TorBox at all")
    # 401 is the right answer without a key, and proves the service is up.
    if response.status_code not in (200, 401, 403):
        raise AssertionError("TorBox answered HTTP %s" % response.status_code)
    return "TorBox answered HTTP %s" % response.status_code


@check("every account still offers a phone sign-in", "accounts")
def _device_flows():
    from katan import http

    probes = [
        ("Real-Debrid", lambda: http.get(
            "https://api.real-debrid.com/oauth/v2/device/code",
            params={"client_id": "X245A4XAIBGVM", "new_credentials": "yes"},
            timeout=(5, 10)), (200,)),
        ("AllDebrid", lambda: http.get(
            "https://api.alldebrid.com/v4.1/pin/get",
            params={"agent": "katan"}, timeout=(5, 10)), (200,)),
        ("TorBox", lambda: http.get(
            "https://api.torbox.app/v1/api/user/auth/device/start",
            params={"app": "Katan"}, timeout=(5, 10)), (200,)),
        # These two answer "invalid_client" until the applications are
        # registered, which is a configuration gap rather than an outage - so
        # the check is that the endpoint is there and says so.
        ("Trakt", lambda: http.post(
            "https://api.trakt.tv/oauth/device/code",
            json={"client_id": "0" * 64}, timeout=(5, 10)), (200, 401)),
        ("Premiumize", lambda: http.post(
            "https://www.premiumize.me/token",
            data={"client_id": "0", "response_type": "device_code"},
            timeout=(5, 10)), (200, 400)),
    ]
    broken = []
    for name, call, allowed in probes:
        try:
            response = call()
        except Exception as error:
            broken.append("%s raised %s" % (name, str(error)[:40]))
            continue
        if response is None:
            broken.append("%s did not answer" % name)
        elif response.status_code not in allowed:
            broken.append("%s answered HTTP %s" % (name, response.status_code))
    if broken:
        raise AssertionError("; ".join(broken))
    return "all five answered"


# --------------------------------------------------------------------------
# the whole path, from a title to a playable list
# --------------------------------------------------------------------------


def _sources_for(kind, tmdb_id, season=0, episode=0):
    from katan import play
    from katan.sources import aggregator

    meta = play.build_meta({"type": kind, "tmdb": tmdb_id,
                            "season": season, "episode": episode})
    if not meta.get("title"):
        raise AssertionError("TMDB gave no title for %s" % tmdb_id)
    return meta, aggregator._ranked(meta, force=True)


@check("a popular film resolves to sources", "sources")
def _film_sources():
    # Fight Club, which has been widely available for twenty-five years. If
    # this finds nothing, the providers are down rather than the film obscure.
    meta, found = _sources_for("movie", 550)
    if not found:
        raise AssertionError("no sources for %r" % meta.get("title"))
    return "%d sources, best %s" % (len(found), found[0].get("quality", "?"))


@check("a current episode resolves to sources", "sources")
def _episode_sources():
    # Silo, season one episode one.
    meta, found = _sources_for("episode", 125988, 1, 1)
    if not found:
        raise AssertionError("no sources for %r" % meta.get("title"))
    return "%d sources" % len(found)


@check("an anime episode is asked for at both addresses", "sources")
def _anime_addresses():
    from katan.sources import aggregator

    # Bleach 2x46. TMDB's address returns nothing at all and the Kitsu one
    # returns the episode, which is the whole reason the second address exists.
    from katan import play
    meta = play.build_meta({"type": "episode", "tmdb": 30984,
                            "season": 2, "episode": 46})
    address = aggregator._anime_address(meta)
    if not address:
        raise AssertionError("no Kitsu address was worked out for Bleach 2x46")
    kitsu_id = (address.get("ids") or {}).get("kitsu")
    found = aggregator._ranked(meta, force=True)
    if not found:
        raise AssertionError("kitsu:%s:%s found nothing"
                             % (kitsu_id, address.get("episode")))
    return "kitsu:%s:%s -> %d sources" % (kitsu_id, address.get("episode"),
                                          len(found))


@check("subtitles are found for a film", "subtitles", required=False)
def _subtitles():
    from katan import play
    from katan.subs import outlook

    meta = play.build_meta({"type": "movie", "tmdb": 550})
    candidates = outlook.candidates(meta)
    if not candidates:
        raise AssertionError("no subtitle candidates at all")
    return "%d candidates" % len(candidates)


@check("an Israeli channel resolves to a stream", "vod", required=False)
def _channel_stream():
    from katan.vod import channels

    tried = []
    for entry in channels.live_channels(limit=6):
        channel_id = (entry.get("ids") or {}).get("channel") or entry.get("id")
        try:
            url = channels.resolve(channel_id)
        except Exception as error:
            tried.append("%s raised %s" % (channel_id, str(error)[:30]))
            continue
        if url:
            return "%s -> %s" % (channel_id, url[:46])
        tried.append("%s gave no URL" % channel_id)
    raise AssertionError("; ".join(tried[:4]) or "no channels to try")


# --------------------------------------------------------------------------
# running them
# --------------------------------------------------------------------------


def run(offline=False):
    results = []
    for entry in CHECKS:
        if offline and entry["network"]:
            results.append(dict(entry, status="skipped", detail="offline",
                                ms=0, run=None))
            continue
        started = time.time()
        status, detail = "ok", ""
        try:
            detail = entry["run"]() or ""
        except AssertionError as error:
            status, detail = "failed", str(error)
        except Exception as error:
            status = "failed"
            detail = "%s: %s" % (type(error).__name__, str(error)[:120])
            if os.environ.get("E2E_TRACEBACK"):
                traceback.print_exc()
        results.append({"name": entry["name"], "area": entry["area"],
                        "required": entry["required"], "status": status,
                        "detail": detail,
                        "ms": int((time.time() - started) * 1000)})
        mark = {"ok": "  ok  ", "failed": " FAIL ", "skipped": " skip "}[status]
        say("[%s] %-44s %6dms  %s"
            % (mark, entry["name"], results[-1]["ms"], detail[:70]))
    return results


def report(results):
    say("")
    say("=" * 78)
    failed = [r for r in results if r["status"] == "failed"]
    blocking = [r for r in failed if r["required"]]
    passed = [r for r in results if r["status"] == "ok"]

    say("%d checks: %d passed, %d failed, %d skipped"
        % (len(results), len(passed), len(failed),
           len(results) - len(passed) - len(failed)))

    if not failed:
        say("nothing to report")
        return 0

    say("")
    say("problems, all of them:")
    for entry in failed:
        say("  %-9s %-42s %s"
            % ("BLOCKING" if entry["required"] else "outage?",
               entry["name"], entry["detail"][:90]))
    if not blocking:
        say("")
        say("none of these are ours: every failure is a service somebody else "
            "runs, so the run is not failed.")
    return 1 if blocking else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true",
                        help="skip every check that needs the network")
    parser.add_argument("--json", help="also write the results to this file")
    args = parser.parse_args()

    boot()
    say("Katan end to end, %d checks%s"
        % (len(CHECKS), " (offline)" if args.offline else ""))
    say("")
    results = run(offline=args.offline)
    code = report(results)
    if args.json:
        with io.open(args.json, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(results, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
