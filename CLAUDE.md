# Katan

A lightweight Netflix-style Kodi 21 add-on, built for weak hardware
(BYINTEK LOVE U4 projector, Mi Box), with Hebrew and English throughout.

## Layout

    plugin.video.katan/       the add-on (three Kodi extension points, one codebase)
      main.py                 plugin entry
      service.py              background service
      subtitles.py            subtitle dialog provider
      resources/
        settings.xml          user settings
        data/                 bundled channel list and VOD catalogue
        language/             en_GB and he_IL strings
        skins/default/1080i/  the custom windows
        lib/katan/            all the Python
    repository.katan/         so devices auto-update
    tools/build.py            builds the zips and the repository index
    tests/                    pytest suite with Kodi stubs

## The rules that keep it light

These are not style preferences. They are why the add-on works on a
four-core A53 with little RAM, and every one of them is enforced by a test.

1. **One bounded worker pool.** All parallel work goes through
   `http.run_parallel`, capped at four workers with a wall-clock deadline.
   Never create a thread per provider.
2. **Server-side aggregators first.** Torrentio, Comet, MediaFusion and Zilean
   crawl on their own servers. One request returns dozens of parsed results.
   Local scrapers run only when they are relevant.
3. **Top-K only.** The picker shows the best eight. Everything else is behind
   "show all".
4. **One subtitle download, not fifty.** Candidates are scored before anything
   is fetched.
5. **Capped storage.** One SQLite cache with a size limit and LRU eviction,
   plus a capped subtitle folder.
6. **Lazy imports.** A feature costs nothing until it is used.
7. **No skin fork, no runtime patching, no vendored add-ons.** There are no
   required dependencies at all. `requests` is used when installed, and
   `urlsession.py` covers the same ground with the standard library when it
   is not, which keeps several megabytes out of memory on a small device.

## How the pieces fit

* `catalog.py` declares the home rows as data. Adding a row is one entry.
  The service warms them, so opening the add-on is a database read.
* `sources/aggregator.py` runs providers, merges by infohash, asks each debrid
  service once in batches which hashes are cached, then ranks.
* `debrid/` has one class per service behind a common interface. The API
  quirks are documented where they matter: Real-Debrid has no bulk cache check
  any more, TorBox caps uncached adds at 60 an hour, Premiumize answers
  batches, AllDebrid has no bulk check either.
* `subs/` picks one subtitle: embedded track, then file hash, then release
  correlation, then AI translation of the best English match.
* `subs/sync.py` is the part that makes a subtitle actually fit. It correlates
  speech activity as big-integer bitmasks, which is fast enough to align a two
  hour film in about 170 ms, and corrects both constant offset and PAL/NTSC
  drift. Its score is chance corrected, so an unrelated subtitle is refused.
* `vod/` is Israeli television. Live channels and the on-demand catalogue are
  separate sections on purpose, because they are browsed differently.

## Testing on Windows

    python tools/setup_kodi.py        portable Kodi 21.3 into .kodi-test/
    .kodi-test/kodi.exe -p            run it

The add-on folders are junctions into the source tree, so an edit here is live
in Kodi with no copy step. The setup also enables both add-ons in the add-on
database, which Kodi does not do for manually placed folders, and turns on
debug logging. Everything lives in `.kodi-test/`; deleting it undoes the lot.

Windows testing proves the logic. It does not prove the device works, because
codecs, memory and storage speed are properties of the hardware. Tools ->
Device report measures those on whatever device it runs on.

## The target device

BYINTEK LOVE U4: 1 GB RAM, 8 GB storage, Android 9, four Cortex-A53 cores,
Mali-G31 MP2, native 1280x720.

It runs the Kodi POV IL build fine, so the baseline is not the problem. What
ended that build on the same hardware was bursts of load: many scrapers at
once and a subtitle search that fetches candidate after candidate. With a
gigabyte shared with Android, Kodi has a few hundred megabytes, and a burst
past that ends the process.

Two consequences shape the code. Concurrency is bounded rather than trusted to
behave, and artwork is treated as a memory budget: Kodi caches decoded bitmaps,
so poster width matters far more than download size. Three visible rows of
posters cost roughly 6 MB at w185 and 22 MB at w342.

## Integration testing

    python tools/drive_kodi.py        start Kodi, open every add-on path via
                                      JSON-RPC, run the device report
    python tools/check_channels.py    resolve every Israeli channel and ask
                                      the stream for a few bytes
    python tools/check_channels.py --update
                                      record what worked into channels.json

`drive_kodi.py` is the test the unit suite cannot be: Files.GetDirectory on a
plugin path runs the plugin exactly as a user would, so a broken route shows up
as an empty or failed directory rather than as a silent blank screen.

Measured on Kodi 21.3, Windows, September 2026: nine paths opened, none failed,
no Python errors. Kodi 21 ships **Python 3.8**, not 3.11, so `int.bit_count`
is unavailable and the popcount fallback in `subs/sync.py` is load-bearing.

## Verifying the windows render

    python tools/check_windows.py     open the custom home window in Kodi and
                                      screenshot it into .kodi-test/shots/

Directory-mode tests never load a single line of window XML, so a malformed
control or a bad id shows only as a window that refuses to open. This opens
the real thing and photographs it.

That check found four defects nothing else could: `$LOCALIZE` does not resolve
add-on strings inside a Python add-on's own window (it needs
`$ADDON[plugin.video.katan 32254]`), the row list was 90px too short so the
second row was clipped, the hero stayed blank until the user moved because it
waited for a focus event, and a channel logo was being stretched across the
whole backdrop.

## Channel data goes stale

Sixteen of the forty-nine television channels no longer play. Keshet 12 and its
backups return an Akamai "Access Denied": those streams are signed with a token
minted by Mako's entitlement service, which this add-on does not implement.
Idan Plus does, which is why they work in the Kodi POV IL build.

Rather than list entries that fail, `check_channels.py --update` records a
`working` flag and the add-on hides the ones that do not play. Thirty-three
television channels and all thirty-five radio stations are verified working.
Re-run the probe whenever channels start failing; it is a data fix, not a
release.

## Device profiles

`profiles.py` holds three sets of settings applied together: low memory,
balanced, powerful. Tools -> Performance profile recommends one from the free
memory, core count and panel resolution the device reports. Low memory caps
resolution at 720p, halves the workers, shrinks the caches and falls back to
the plain Kodi lists rather than the custom windows, which hold every visible
row of artwork at once.

## Working on this

    python -m pytest tests            run everything
    python tools/build.py             build the zips and repository index
    python tools/build.py --check     validate without writing

The test suite runs against stubs in `tests/stubs`, so no Kodi install is
needed. `test_imports.py` imports every module, which is the cheapest way to
catch the errors Kodi would only show as a blank screen.

## The subtitle chooser hierarchy

What the viewer sees when they open the subtitle list, in order:

    100% embedded    a track inside the file, so exactly in time by
                     construction. Listed first and costs nothing, because
                     Kodi has already demuxed the file it is playing.
    100%             an exact match: the same release name, or the same file
                     confirmed by hash. No qualifier, because none is needed.
    82% estimate     everything else, shown as an estimate so a guess reads
                     as a guess rather than a promise.

A forced or signs-only embedded track is scored lower and labelled as such. It
captions on-screen text rather than translating dialogue, so the automatic path
skips it entirely and the chooser warns before the viewer picks it.

`subs/embedded.py` owns the listing and both the chooser and the automatic path
use it, because two implementations of "find the Hebrew track in this file"
would answer differently the moment either changed.

## What was taken from Kodi POV IL, and what was not

Their MoranSubs add-on is 152 files and 2.5 MB of Python for subtitles alone.
Two of its ideas are genuinely good and are implemented here; one is not worth
its cost.

**Taken: gender context.** Hebrew marks the speaker's gender on verbs and
adjectives, so "I am tired" has two correct translations. English carries no
such information, which is why English to Hebrew machine translation reads as
obviously foreign. They solve it by downloading an *Arabic* subtitle as a
gender oracle and aligning it line by line, which costs an extra download, an
alignment pass and a much larger prompt.

`subs/ai/context.py` gets most of the same benefit for a few hundred bytes: the
TMDB cast list, which already comes with a gender per person and which the
add-on already fetches for the details screen, is named in the prompt. The same
insight also picks the translation source: a Spanish or Arabic subtitle carries
gender through for free, so it wins a close call against English.

**Taken: progressive delivery.** Translating a feature film takes minutes.
Showing each finished chunk as it arrives means the viewer starts watching
almost immediately. Kodi caches a subtitle by path, so the file name alternates
between two slots to force a re-read.

**Not taken: extracting embedded subtitles from a remote file.** They ship an
818 line Matroska parser and a 155 KB extractor that read the container over
HTTP ranges, with a deadline of up to 900 seconds. Subtitle blocks are spread
across every cluster, so getting them means pulling a large fraction of an
8 GB file. On a device with a gigabyte of RAM that is the wrong trade. Kodi
already demuxes the file it is playing, so selecting an embedded Hebrew track
costs nothing and is done first.

## Known gaps

Ordered by how much they matter. Nothing here is a stub pretending to work:
settings that promised a provider with no code behind them were removed, and
`test_addon_integrity.py` now fails if one comes back.

**Needs a real device or account to finish**

* Debrid playback has never run against a live account. Every client is
  written to its documented API and unit tested against fixtures, but no
  stream has actually been resolved end to end.
* Trakt needs the user's own client id and secret, because the project has no
  registered application.
* The Kan and Mako episode extractors use a ladder of strategies and have not
  been checked against the live sites.

**Missing features**

* Sixteen live channels, Keshet 12 among them, are signed with an Akamai token
  minted by the broadcaster's entitlement service. They are hidden rather than
  listed as broken. Implementing the token flow is the single biggest win for
  Israeli live TV.
* VOD extractors exist for Kan and Mako only. Reshet, Sport 5, Sport 1,
  Channel 14 and 891FM list their programmes but cannot open them, which is
  most of the 2,810 entry catalogue.
* No Ktuvit subtitle provider. Wizdom, OpenSubtitles and SubSource cover a
  lot of Hebrew, but Ktuvit is the largest source and needs a login flow.
* No MDBList, no SeaDex anime rankings, no kids mode, no TMDb Helper player
  file, no Israeli torrent scrapers.

**Not started**

* `repository.katan/addon.xml` still has USER/REPO placeholders.

## Attribution

The Israeli channel list and VOD catalogue were derived from the Idan Plus
add-on by Fishenzon (github.com/Fishenzon/repo), which is what the Kodi POV IL
build uses for Israeli content.
