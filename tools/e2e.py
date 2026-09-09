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
# the things that differ between a PC and a television box
#
# The add-on is pure Python and runs the same code everywhere, so what varies
# is not the logic but the ground underneath it: the filesystem, the path
# rules and the text encoding. Those are exactly what a run on one operating
# system cannot tell you about the other.
#
# Windows forbids characters Android allows and compares filenames without
# case; Android is case sensitive and allows nearly anything. A subtitle
# written as one name and read back as another works on one and not the other,
# and the symptom is a subtitle that silently never appears.
# --------------------------------------------------------------------------


@check("subtitle names survive this filesystem", "platform", network=False)
def _subtitle_names():
    from katan import kodi
    from katan.subs import auto

    folder = os.path.join(kodi.profile_path(), "platform-check")
    if not os.path.isdir(folder):
        os.makedirs(folder)

    # A Hebrew title, a Japanese one and a punctuation-heavy release name.
    # str.isalnum() is true for Hebrew and Japanese letters, so an ASCII strip
    # that looks right strips nothing at all from any of these.
    titles = [u"\u05d4\u05d7\u05d1\u05e8\u05d9\u05dd \u05e9\u05dc \u05e0\u05d0\u05d5\u05e8",
              u"\u9b3c\u6ec5\u306e\u5203",
              u'A: "Film" / Part 2 <one> | two?']
    written = []
    for title in titles:
        name = auto.name_for({"type": "movie", "title": title, "ids": {}}, "he")
        path = os.path.join(folder, name)
        try:
            with io.open(path, "w", encoding="utf-8") as handle:
                handle.write(u"1\n00:00:01,000 --> 00:00:02,000\n\u05e9\u05dc\u05d5\u05dd\n")
        except (OSError, ValueError) as error:
            raise AssertionError("%r could not be written as %r: %s"
                                 % (title[:14], name, str(error)[:60]))
        if not os.path.isfile(path):
            raise AssertionError("%r wrote to %r and it is not there"
                                 % (title[:14], name))
        written.append(name)

    if len(set(written)) != len(written):
        raise AssertionError("two different titles produced the same filename: %s"
                             % ", ".join(written))
    return "%d names, longest %d characters" % (len(written),
                                                max(len(n) for n in written))


@check("filenames do not rely on case", "platform", network=False)
def _case_sensitivity():
    """A name that differs only in case is a different file on Android.

    Windows would hand back the first one and nobody would notice until the
    add-on reached a television box, where the second write lands somewhere
    else and the subtitle is simply never found.
    """
    from katan import kodi
    from katan.subs import auto

    folder = os.path.join(kodi.profile_path(), "platform-check")
    if not os.path.isdir(folder):
        os.makedirs(folder)

    lower = auto.name_for({"type": "movie", "ids": {"imdb": "tt0137523"}}, "he")
    upper = auto.name_for({"type": "movie", "ids": {"imdb": "TT0137523"}}, "he")
    if lower != upper:
        raise AssertionError(
            "the same film produced %r and %r, which are one file on Windows "
            "and two on Android" % (lower, upper))
    return "%r either way" % lower


@check("the cache works under an awkward profile path", "platform",
       network=False)
def _awkward_profile():
    """Kodi's userdata path is not ours to choose.

    On Android it sits under /storage/emulated/0/Android/data, on Windows
    under a user folder that very often has a space and sometimes a name in
    another script.
    """
    from katan import cache, kodi

    original = kodi.profile_path
    awkward = os.path.join(WORK, u"a folder \u05e2\u05d1\u05e8\u05d9\u05ea (2)")
    try:
        if not os.path.isdir(awkward):
            os.makedirs(awkward)
        kodi.profile_path = lambda: awkward
        cache.close()
        cache.set("platform|probe", {"value": u"\u05e9\u05dc\u05d5\u05dd"}, 60)
        back = cache.get("platform|probe")
        if not back or back.get("value") != u"\u05e9\u05dc\u05d5\u05dd":
            raise AssertionError("wrote to the cache and read back %r" % (back,))
    finally:
        cache.close()
        kodi.profile_path = original
        cache.close()
    return "sqlite opened under %r" % os.path.basename(awkward)


@check("the built zip is portable", "platform", network=False)
def _zip_portable():
    """A zip is unpacked by Kodi on whatever box installed it.

    Two things make one fail on a television box and not on the machine that
    built it: a backslash in a member name, which Android reads as part of the
    filename rather than a folder, and two members differing only in case,
    which silently overwrite each other on Windows and not on Android.
    """
    import zipfile

    target = os.path.join(ROOT, "repo", "zips", "plugin.video.katan")
    zips = sorted(f for f in os.listdir(target)) if os.path.isdir(target) else []
    zips = [f for f in zips if f.endswith(".zip")]
    if not zips:
        raise AssertionError("no built zip in repo/zips - run tools/build.py")

    with zipfile.ZipFile(os.path.join(target, zips[-1])) as archive:
        names = archive.namelist()
    backslashes = [n for n in names if "\\" in n]
    if backslashes:
        raise AssertionError("%d members carry a backslash, e.g. %r"
                             % (len(backslashes), backslashes[0]))
    folded = {}
    for name in names:
        folded.setdefault(name.lower(), []).append(name)
    clashes = [v for v in folded.values() if len(v) > 1]
    if clashes:
        raise AssertionError("members differing only in case: %s" % clashes[0])
    return "%s, %d members" % (zips[-1], len(names))


@check("Hebrew reaches the log on this platform", "platform", network=False)
def _log_encoding():
    """Writing a Hebrew title to the log must not raise.

    Not hypothetical: the tools in this folder crashed twice with
    UnicodeEncodeError printing exactly these titles, because a Windows
    console defaults to cp1252 while Android is UTF-8 throughout.
    """
    from katan import kodi

    title = u"\u05d4\u05d7\u05d1\u05e8\u05d9\u05dd \u05e9\u05dc \u05e0\u05d0\u05d5\u05e8 - \u9b3c\u6ec5\u306e\u5203"
    kodi.log("platform check: %s" % title)
    kodi.log_error("platform check: %s" % title)
    return "logged %d characters without raising" % len(title)


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


@check("the published index answers anonymously", "updates")
def _index_published():
    """Kodi fetches a repository with no credentials of any kind.

    So the one thing that matters about the hosting is that it works for
    somebody who is not logged in to anything. The three URLs used to point at
    raw.githubusercontent.com on a private repository, which answers 404 to
    exactly that request - and the add-on had no way to notice.
    """
    from katan import http, updater

    url = updater.index_url()
    response = http.get(url, timeout=(5, 12))
    if response is None:
        raise AssertionError("no answer from %s" % url)
    if response.status_code != 200:
        raise AssertionError("%s answered HTTP %s" % (url, response.status_code))
    if b"<addons" not in response.content:
        raise AssertionError("%s is not a repository index" % url)
    return "%s, %d bytes" % (url.rsplit("/", 2)[-2], len(response.content))


@check("the index offers a real, downloadable release", "updates")
def _release_downloadable():
    """The version in the index and the zip beside it have to agree.

    They are written by two different steps - release.py bumps addon.xml,
    build.py names the zip - and the download URL is *derived* from the
    version rather than stored, so a mismatch between them is silent until
    somebody presses update and gets a 404.
    """
    import re
    import xml.etree.ElementTree as ET

    from katan import http, updater

    response = http.get(updater.index_url(), timeout=(5, 12))
    root = ET.fromstring(response.content)
    published = ""
    for node in root.findall("addon"):
        if node.get("id") == updater.ADDON_ID:
            published = node.get("version") or ""
            break
    if not published:
        raise AssertionError("the index does not list %s" % updater.ADDON_ID)
    if not re.match(r"^\d+\.\d+", published):
        raise AssertionError("published version %r is not a version" % published)

    base = updater.index_url().rsplit("/", 1)[0]
    zip_url = "%s/zips/%s/%s-%s.zip" % (base, updater.ADDON_ID,
                                        updater.ADDON_ID, published)
    head = http.get(zip_url, timeout=(5, 20))
    if head is None or head.status_code != 200:
        raise AssertionError("%s answered %s"
                             % (zip_url, head.status_code if head else "nothing"))
    if not head.content.startswith(b"PK"):
        raise AssertionError("what is published is not a zip")
    return "%s, %d KB" % (published, len(head.content) // 1024)


@check("the published release actually installs", "updates")
def _published_release_installs():
    """Download what is on the site and install it, rather than trusting it.

    Everything else about updates is checked against a zip this machine built.
    This is the one that asks the question the viewer asks: the bytes actually
    being served, unpacked over an actual installed copy, with actual keys
    beside it - do the keys survive and does the version move.

    Installed into a temporary tree, never over the real add-on - and that
    sentence is why the guard below exists. `boot()` sets `kodi.addon_path`
    to the real add-on so the bundled channel data can be read, and
    `updater.apply` replaces whatever that function points at. Pointing the
    stub's addon somewhere else is not enough, because nothing here reads the
    stub; the module-level function is what has to move, and it has to move
    back. Writing this the obvious way unpacked the published release over
    the working tree.
    """
    import shutil
    import tempfile
    import xml.etree.ElementTree as ET

    from katan import http, kodi, updater

    published = ""
    response = http.get(updater.index_url(), timeout=(5, 12))
    for node in ET.fromstring(response.content).findall("addon"):
        if node.get("id") == updater.ADDON_ID:
            published = node.get("version") or ""
    if not published:
        raise AssertionError("the index does not list %s" % updater.ADDON_ID)

    base = updater.index_url().rsplit("/", 1)[0]
    zip_url = "%s/zips/%s/%s-%s.zip" % (base, updater.ADDON_ID,
                                        updater.ADDON_ID, published)
    body = http.get(zip_url, timeout=(5, 30))
    if body is None or body.status_code != 200:
        raise AssertionError("%s answered %s" % (zip_url, body and body.status_code))

    sandbox = tempfile.mkdtemp(prefix="katan-e2e-install-")
    was_addon_path = kodi.addon_path
    was_profile_path = kodi.profile_path
    try:
        addons = os.path.join(sandbox, "addons")
        installed = os.path.join(addons, updater.ADDON_ID)
        profile = os.path.join(sandbox, "addon_data", updater.ADDON_ID)
        os.makedirs(installed)
        os.makedirs(profile)
        # An installed copy that is plainly older, and a key beside it.
        io.open(os.path.join(installed, "addon.xml"), "w",
                encoding="utf-8").write(
                    '<addon id="%s" version="0.0.0"/>' % updater.ADDON_ID)
        io.open(os.path.join(profile, "settings.xml"), "w",
                encoding="utf-8").write(
                    '<settings version="2"><setting id="tmdb.apikey">'
                    'KEEP-ME</setting></settings>')

        release = os.path.join(sandbox, "release.zip")
        with io.open(release, "wb") as handle:
            handle.write(body.content)
        if not updater._is_sane_zip(release):
            raise AssertionError("the published zip failed its own sanity check")

        kodi.addon_path = lambda: installed
        kodi.profile_path = lambda: profile
        # The guard, not a formality: apply() deletes and replaces whatever
        # addon_path names, so it must be provably inside the sandbox before
        # it is allowed to run.
        target = os.path.normpath(kodi.addon_path())
        if not target.startswith(os.path.normpath(sandbox) + os.sep):
            raise AssertionError("refusing to install over %s" % target)

        if updater.apply(release) is not True:
            raise AssertionError("installing the published release failed")

        landed = ET.parse(os.path.join(installed, "addon.xml")).getroot().get(
            "version")
        if landed != published:
            raise AssertionError("installed %s after publishing %s"
                                 % (landed, published))
        kept = io.open(os.path.join(profile, "settings.xml"),
                       encoding="utf-8").read()
        if "KEEP-ME" not in kept:
            raise AssertionError("the update took the viewer's key with it")
        leftovers = [n for n in os.listdir(addons) if n != updater.ADDON_ID]
        if leftovers:
            raise AssertionError("left behind: %s" % leftovers)
    finally:
        # Restored before anything else runs: every later check reads the
        # bundled channel and VOD data through these.
        kodi.addon_path = was_addon_path
        kodi.profile_path = was_profile_path
        shutil.rmtree(sandbox, ignore_errors=True)

    return "0.0.0 -> %s, %d KB, key intact" % (published,
                                               len(body.content) // 1024)


@check("a version that does not exist is refused, not invented", "updates")
def _missing_release_is_a_404():
    """The check above only means something if a miss can be told apart.

    Cloudflare Pages treats a site with a root index.html and no 404.html as a
    single-page application and answers every unmatched path with that index,
    status 200. The per-folder listings that make the site browsable from a
    television introduced exactly that, and it went unnoticed because every
    path anybody tested existed. `updater.check` decides on `status_code >=
    400`, so under that fallback a withdrawn or misnamed release reads as
    present and fails later, in the download rather than the lookup.
    """
    from katan import http, updater

    base = updater.index_url().rsplit("/", 1)[0]
    url = "%s/zips/%s/%s-99.99.99.zip" % (base, updater.ADDON_ID,
                                          updater.ADDON_ID)
    response = http.get(url, timeout=(5, 20))
    if response is None:
        raise AssertionError("no answer from %s" % url)
    if response.status_code != 404:
        raise AssertionError(
            "a release that does not exist answered HTTP %s (%d bytes of %s)"
            % (response.status_code, len(response.content),
               response.headers.get("Content-Type", "?")))
    return "HTTP 404, as it should be"


@check("a newer version would be offered and an older one refused", "updates",
       network=False)
def _version_comparison():
    """The comparison is numeric, and it has to be.

    As text "0.1.10" sorts below "0.1.9", so the tenth patch release of any
    line would look like a downgrade and never be offered. Anything
    unparseable sorts lowest, so a corrupt index cannot trigger an update.
    """
    from katan import updater

    cases = [("0.1.2", "0.1.1", True), ("0.1.10", "0.1.9", True),
             ("1.0.0", "0.9.9", True), ("0.1.1", "0.1.1", False),
             ("0.1.0", "0.1.1", False), ("", "0.1.1", False),
             ("not-a-version", "0.1.1", False)]
    wrong = []
    for published, installed, expected in cases:
        newer = updater.parse_version(published) > updater.parse_version(installed)
        if newer != expected:
            wrong.append("%r against %r said %s" % (published, installed, newer))
    if wrong:
        raise AssertionError("; ".join(wrong))
    return "%d comparisons" % len(cases)


@check("the update path works without the requests module", "updates")
def _stdlib_update_path():
    """Which is what the projector has.

    `requests` is optional and ships on nothing by default, so on a television
    box every one of these fetches goes through urlsession.py instead. That is
    a different HTTP client, a different TLS path and a different User-Agent,
    against a CDN that does refuse some clients - a bare urllib request to this
    same index answers 403. Worth proving rather than assuming, because the
    machine that would notice is the one furthest from here.
    """
    from katan import http, updater

    was = http.HAVE_REQUESTS
    try:
        http.HAVE_REQUESTS = False
        http.close_session()
        http._session = None

        index = http.get(updater.index_url(), timeout=(5, 12))
        if index is None or index.status_code != 200:
            raise AssertionError(
                "the index answered %s without requests"
                % (index.status_code if index else "nothing"))
        if b"<addons" not in index.content:
            raise AssertionError("what came back is not a repository index")
        backend = type(http.session()).__module__
    finally:
        http.HAVE_REQUESTS = was
        http.close_session()
        http._session = None
    return "%s, %d bytes" % (backend.rsplit(".", 1)[-1], len(index.content))


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
