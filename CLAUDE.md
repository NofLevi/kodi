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
        players/              the TMDb Helper player file
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
  correlation, then AI translation of the best English match. Wizdom and
  SubSource are anonymous; Ktuvit is a members' site, so it is off until an
  account is entered and is asked after the faster sources.
* `meta/seadex.py` is the exception to ranking by numbers. For anime the
  release group *is* the quality, and SeaDex publishes which group won. It
  returns infohashes, the aggregator already merges by infohash, so a
  recommendation is a set of hashes to recognise rather than a name to match.
* `kids.py` replaces the home rows rather than filtering them. A TMDB list
  result carries no certification at all, so a filter alone would let
  everything through; the kid-safe rows ask TMDB for a ceiling instead.
* `subs/sync.py` is the part that makes a subtitle actually fit. It correlates
  speech activity as big-integer bitmasks, which is fast enough to align a two
  hour film in about 170 ms, and corrects both constant offset and PAL/NTSC
  drift. Its score is chance corrected, so an unrelated subtitle is refused.
* `vod/` is Israeli television. Live channels and the on-demand catalogue are
  separate sections on purpose, because they are browsed differently. Each
  broadcaster gets one small module under `vod/extractors/`, and all seven in
  the catalogue now have one: Kan and Mako scrape pages, Reshet talks to
  Kaltura OTT, Sport 5 and Now 14 reduce a large JSON document before caching
  it, Sport 1 hops through Walla, and 891FM reads an hourly radio schedule.
* `vod/entitlement.py` mints the Akamai ticket Mako requires. It is worth
  reading for the two measurements that make it cheap: the grant is `acl=/*`,
  so one ticket signs every Keshet channel, and Akamai rewrites the variant
  URLs inside the master manifest with a much longer lived token, so only the
  first request needs signing and playback survives the ticket expiring.

## Testing on Windows

The portable Kodi is **not** in the repository: it is 232 MB of downloadable
binaries. Fetch it on any machine with one command.

    python tools/setup_kodi.py        portable Kodi 21.3 into .kodi-test/
    .kodi-test/kodi.exe -p            run it

That step also links the add-on folders in, enables them in Kodi's add-on
database (Kodi leaves manually placed add-ons disabled), and turns on debug
logging. Everything lives under `.kodi-test/`, which is gitignored; deleting
the folder undoes all of it.

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

## The test suite

700 tests, all running against Kodi stubs, so no Kodi install is needed:

    python -m pytest tests

`tests/stubs/` is a small fake Kodi: `xbmc`, `xbmcgui`, `xbmcaddon`, `xbmcvfs`
and `xbmcplugin` in about 480 lines. It records what the add-on did rather than
drawing anything, so a handler can be run and then inspected. `conftest.py`
gives every test a fresh profile directory, empty settings and a clean cache,
plus a `no_network` fixture that fails loudly if a test reaches the internet.

| File | Tests | What it protects |
|---|---|---|
| `test_imports.py` | 91 | Imports every module. Kodi reports an import error as a blank screen, so this is the cheapest bug-catcher in the suite. Also fails on invalid escape sequences, stray control characters, and `"%s" % (a, b).strip()` - where the method binds to the tuple, not the string, which has shipped twice and once took the whole source picker down. |
| `test_addon_integrity.py` | 20 | What is invisible until Kodi loads the add-on: addon.xml validity, entry points and assets existing, every settings id having a default and matching it, skin XML parsing, textures existing, string files well formed, every localize id having a string, every provider setting having a module, every url_for naming a real route, every window class having its XML, the TMDb Helper player file naming only registered actions, and the four things a real Kodi taught us - settings labels being string ids, empty string defaults declaring allowempty, every setting the code uses being declared, and the row area holding a whole number of rows. |
| `test_core.py` | 14 | The SQLite cache and the router: TTLs, compression, LRU eviction under the size cap, and url_for round-tripping through parse_params. |
| `test_routes.py` | 22 | Dispatches every route the way Kodi does, network blocked. Catches wiring mistakes that would otherwise show as an empty screen, and stops a row that is known to be empty being offered as a menu entry that leads nowhere. |
| `test_aggregator.py` | 22 | The orchestration the well-tested pieces hang off: top-K against "show all" (which used to return the same eight rows it was toggling away from), a debrid cache flag that must be able to come *down*, one batched question for a torrent three providers reported, and a provider that raises not taking the search with it. |
| `test_providers.py` | 17 | The Stremio adapter three of the four providers speak. Mostly about payloads that are not shaped the way the last one was: a size sent as a string or a float, a fileIdx that is not a number, and one unreadable stream costing only itself. |
| `test_anilist.py` | 10 | The anime catalog, and specifically that an outage upstream produces a hidden row and a log line rather than a broken screen. A failure is not cached as a result, so the row is retried rather than staying empty for the TTL. |
| `test_release_parser.py` | 29 | Resolution, source, codec, HDR, release group, season and episode, absolute anime numbering, Hebrew hints. Source ranking and subtitle matching both depend on it. |
| `test_sources.py` | 21 | Merging the same torrent from several providers, and the filter and ranking rules: resolution ceiling, disabled codecs, HDR, cam releases, implausible sizes, cached-only, and a cached source always beating an uncached one. |
| `test_seadex.py` | 15 | The anime exception: a curated pick beating a far more seeded release, matching by infohash so a lookalike can never be promoted, a cached source still winning, an uncovered title costing nothing, and a broken SeaDex not breaking the picker. |
| `test_debrid.py` | 11 | Picking the right file from a season pack, ignoring samples and extras, refusing to play the wrong episode, and a repeated cache question not becoming a repeated API call. |
| `test_subtitle_matching.py` | 14 | Candidate scoring: hash match, identical release name, group, source, resolution, and the wrong episode pushed to the bottom. Plus the OpenSubtitles hash arithmetic. |
| `test_subtitle_sync.py` | 10 | The alignment engine: constant offset, PAL/NTSC drift, refusing to shift an unrelated subtitle, and a feature-length alignment staying inside its time budget. |
| `test_subtitle_chooser.py` | 17 | The hierarchy the viewer sees: embedded first, then exact, then estimates, with the label each earns. Forced tracks marked and skipped. |
| `test_subtitle_pipeline.py` | 13 | The whole decision end to end: only one file ever downloaded, a hash-matched reference re-timing a mismatched subtitle, translation falling back correctly, and partial translations reaching the player while the rest runs. |
| `test_ktuvit.py` | 19 | The only subtitle provider with an account: the password hashed on the wire, one login per day rather than per search, a stale session re-established exactly once, and the whole provider staying silent without credentials. |
| `test_translation.py` | 13 | The translator surviving a model that misbehaves: code fences, prose around the JSON, blank entries, chunks that fail and must be split. Timings must never move. |
| `test_translation_context.py` | 17 | Cast and gender reaching the prompt, and a gender-marking source language winning a close call without overriding a clearly better match. |
| `test_vod.py` | 20 | Israeli live TV and the catalogue: broadcaster ordering, referers carried through, relative paths given their CDN host, broken channels hidden, Hebrew substring search, and updating the bundled data invalidating the cache. |
| `test_entitlement.py` | 24 | The broadcaster ticket: one Akamai ticket covering every Keshet channel rather than one each, the on-demand CDN getting an AWS ticket instead because the two are not interchangeable, browsing never asking for one, a refusal leaving the URL unsigned rather than empty, and a failure never being cached. |
| `test_kan_mako.py` | 15 | An episode being a descendant of its programme, which is what stops the site's own navigation menu being listed as episodes, and Mako's on-demand streams being signed. |
| `test_reshet.py` | 19 | Reshet's numbering, which the broadcaster publishes wrongly. Season and episode come from the Hebrew title because the metadata fields disagree with it, and the API returns episodes unsorted. |
| `test_sport5.py` | 22 | Six megabytes of broadcaster JSON reduced before it is cached, a season's clips gathered into its programme, the manifest lifted out of the player URL, and the byte order mark that made the whole document unparseable. |
| `test_extractors_israeli.py` | 23 | Now 14, Sport 1 and 891FM, plus the check that every broadcaster in the catalogue has an extractor behind it. |
| `test_mdblist.py` | 19 | The list resolution staying bounded, the curator's order surviving lookups that finish out of order, a title TMDB does not know being dropped rather than blanked, and the API key staying out of the cache keys. |
| `test_kids.py` | 25 | Kids mode replacing the rows rather than filtering them, a pinned row order not being inherited, a warm cache not defeating it, the PIN being stored hashed and actually required to leave, and `catalog.peek` still saying None for a row that was never warmed. |
| `test_windows.py` | 30 | The home and search windows: rows filled lazily, the hero following focus, the on-screen keyboard opening on the script the interface is written in, suggestions never overwriting what was typed, entering the add-on landing in the Katan window, preloading past rows that come back empty, and typing surviving a Kodi whose Action has no getUnicode. |
| `test_details_window.py` | 13 | Information, seasons, episodes, back stepping out of the episode list before closing, and playing a show picking the next unwatched episode - walking on to the next season when one is finished, and never landing on the specials. |
| `test_sources_window.py` | 13 | The picker, which was crashing on every cached source before it had any tests at all. |
| `test_play.py` | 20 | From "the user pressed OK" to "Kodi has a URL": the autoplay decision, the service a cached source goes to, and whether a download may be started. |
| `test_profiles.py` | 25 | Every low-memory setting actually lowering load, all profiles setting the same keys so switching leaves nothing stale, **the shipped defaults being the lean profile key for key**, and the visual-polish switch raising artwork without ever lowering a richer profile. |
| `test_wizard.py` | 7 | The one setup step that is not an account: light against richer artwork, with what each costs, and a device that is told it has room rather than quietly switched. |
| `test_urlsession.py` | 13 | The standard-library HTTP session that replaces requests: parameters, form and JSON bodies, gzip, charsets, and an HTTP error being a response rather than an exception. |
| `test_upnext.py` | 5 | The next episode, including across a season boundary, and the signal being well formed. |
| `test_packaging.py` | 5 | The built zip staying under 600 KB, containing no build junk, rooted at the add-on id, and carrying every file the add-on needs. |

Three of these catch whole classes of mistake rather than one bug:
`test_imports.py` finds anything that will not load, `test_addon_integrity.py`
finds settings and routes that promise something with no code behind them, and
`test_packaging.py` stops the add-on quietly growing.

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
no Python errors. Home 108 ms, live TV 118 ms, search 406 ms. Kodi 21 ships
**Python 3.8**, not 3.11, so `int.bit_count` is unavailable and the popcount
fallback in `subs/sync.py` is load-bearing.

The device report on the shipped defaults: **about 6 MB of visible artwork**,
home from cache 1 ms, cache write 0 ms, subtitle alignment 367 ms, cache on
disk 0.5 MB. The zip is 358 KB against a 600 KB budget.

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

Running it again later found seven more, and they are the reason this section
exists. None of them were visible from Python, and three had been introduced
by changes the unit suite passed cleanly.

* **Kodi's settings format takes string ids, not text.** `label="Accounts"`
  renders as nothing. Every label in the settings dialog was blank, and the
  English was unreachable to a translator. 134 strings are now ids in the
  30000-30999 range, with Hebrew for all of them.
* **An empty string default has to declare itself.** `<default></default>`
  makes Kodi log "error reading the default value" and drop the setting
  entirely, so it can be neither shown nor written. Twenty settings - every
  API key and debrid token - did not exist as far as Kodi was concerned. The
  form it wants is `<default/>` plus an `allowempty` constraint.
* **A setting that settings.xml never declares cannot be written.** Reading
  looks fine because `settings.get` falls back to `DEFAULTS`, which is exactly
  why nobody noticed that the wizard's OAuth tokens and the chosen device
  profile were being written into nothing. Twenty-three of those.
* **A control Kodi has not yet decided is visible cannot take focus.** Setting
  a property and calling `setFocusId` in the same `onInit` pass leaves the
  control hidden at the moment focus is asked for, so both windows logged
  "has been asked to focus, but it can't" and the arrow keys moved around the
  top bar instead of the content. Both now set their properties in a
  `prepare()` before `doModal`.
* **`xbmcgui.Action` has no `getUnicode` on Kodi 21.** Every keypress in the
  search window raised an AttributeError. The suite missed it because its own
  fake Action had grown the method, so the tests asserted against an API Kodi
  does not have.
* The row area was 60px short of two rows, and a grouplist scrolls rather than
  crops, so it slid up and took the first row's heading off the top.
* Kan and Mako listed their own site navigation as episodes, so the first
  "episode" of every programme was a menu item that plays nothing.

A later pass found three more, none of which any test could have shown:

* **Every button was white text on a near-white focus texture.** The focused
  control - the only one whose label matters at that moment - was the one you
  could not read. Forty-four buttons across four windows now declare a
  `focusedcolor`. The search window's on-screen keyboard was the worst case:
  thirty-five keys, and the one under the cursor invisible.
* "Add to Trakt watchlist" is four Hebrew words and one Latin one, and did not
  fit in 270 px. It was clipped at both ends, so the label read as a fragment
  with no beginning.
* The search window opened on the **Latin** keyboard in a Hebrew interface,
  for a catalogue titled entirely in Hebrew.

The lesson worth keeping: the stubs can only be as right as our belief about
Kodi, and three of these were the stubs being more generous than the real
thing. Anything about how Kodi *renders* or *validates* has to be checked in
Kodi.

And one about the checking itself. **Kodi renders lazily when idle** - FPS
drops to 2-5 - so a screenshot taken while nothing is moving returns the
previous frame. That misread cost three false bug reports in one night: a
picker "showing one row", then "none", then a details window "drawing no
poster". All three were fine. Send an input and wait before every capture.

## Channel data goes stale

`check_channels.py --update` records a `working` flag and the add-on hides the
entries that do not play, because a list where a third of the entries fail is
worse than a shorter list that works. Re-run the probe whenever channels start
failing; it is a data fix, not a release.

Measured September 2026: **81 of 84 channels stream**, forty-six television and
all thirty-five radio. Three do not, and all three are genuinely gone rather
than unimplemented:

* `ch_12c`, the Keshet closed-captions feed, answers 400 on every path tried,
  including the ones its sister channels use.
* `ch_bb` and `ch_bbb`, two Big Brother 26 feeds, no longer resolve in DNS.

The thirteen Keshet channels that used to be hidden now work: they needed the
Akamai ticket, which `vod/entitlement.py` now mints, and Keshet had also moved
from CloudFront to `mako-streaming.akamaized.net`. Hidabroot 97 moved to its
own CDN and was repointed.

## Kids mode

`kids.py` does not filter the home screen, it replaces it. That is a design
decision worth knowing before changing it: a TMDB list result carries no
certification at all, so a filter that waits to see one either blocks
everything or lets everything through. The kid-safe rows ask TMDB for a
certification ceiling and family genres instead, so they are safe by
construction, and filtering stays as a second line for anything that does
arrive with metadata. Leaving the mode needs the PIN, which is stored as a
salted hash.

Adding kids mode also fixed a bug that had nothing to do with it: `items.py`
read only TMDB's full genre objects, which appear on a details call, and
ignored the bare `genre_ids` a list result sends, so every row item arrived
with no genres at all.

## Device profiles, and lightweight by default

`profiles.py` holds three sets of settings applied together: low memory,
balanced, powerful. Tools -> Performance profile recommends one from the free
memory, core count and panel resolution the device reports.

**`settings.DEFAULTS` is `LOW_MEMORY`, key for key, and a test asserts it.**
That is the whole of "lightweight by default": the state you get before
touching anything is the lean profile, not a fourth opinion nobody maintains.
It had drifted into being `balanced`, so an add-on written for a projector with
a gigabyte of RAM shipped w342 posters and four workers to it.

Artwork is why this matters more than the rest put together. Kodi holds decoded
bitmaps, so a w342 poster occupies about 700 KB against 205 KB at w185, and
three visible rows is roughly 22 MB against 6 MB. `profiles.artwork_megabytes`
is the single implementation of that arithmetic; the device report and the
setup wizard both call it.

The polish is one switch rather than a profile name: `ui.rich_visuals`, off, in
the interface settings and as a step in the setup wizard. It is a **floor, not
an assignment** - it lifts artwork to at least w342 and twenty a row and cannot
lower a profile that already asks for more. `profiles.set_rich_visuals` is what
turns it on, because the poster width lives in the profile tables and flipping
the setting alone would change nothing until a profile was next applied.

Nothing is raised for you on better hardware. `recommend()` reads the device
and the wizard shows its opinion, but spending the memory stays a choice
somebody makes.

Note that Kodi writes every declared setting into its own file the first time
it loads an add-on, so changing a default only affects a **fresh** install.

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

* Trakt needs the user's own client id and secret, because the project has no
  registered application.
* Ktuvit is implemented against its documented flow and tested against
  fixtures, but has never signed in to a real account.
* MDBList is implemented and fixture tested; the live API needs a key.
* AI subtitle translation is fixture tested; the live path needs a Gemini key.
* Subtitles have run on a real playback - the file hash is computed in about a
  second and the Hebrew search starts - but no subtitle has been watched
  through to the end of a film on a real file.
* Playback itself is no longer on this list. All seven broadcasters, the
  ticket-signed live channels **and debrid streams** have been played in a real
  Kodi 21 with the player reporting speed=1: three films, two episodes, and a
  73-file season pack from which the right episode was picked.
* Mako's on-demand catalogue carries pre-roll ads: a stream takes about
  fifteen seconds to reach the programme. Two of twenty episodes sampled
  resolved to Mako's own "unavailable" clip, which is expired content rather
  than a fault.

**Broken upstream, nothing to fix here**

* **The AniList API is refusing every request** as of 7 September 2026 - 403
  with "The AniList API has been temporarily disabled due to severe stability
  issues", to any User-Agent including a browser one. The anime row therefore
  comes back empty and is hidden, and anime search returns nothing. It will fix
  itself; nothing here needs changing when it does.

**Missing features**

* **No Israeli torrent scraper, and there does not appear to be one to
  write.** This was investigated rather than assumed: the Israeli trackers are
  private and account-gated, Sdarot was dissolved in 2023, and Torrentio's
  `language=hebrew` priority was tested against the live service and returns
  results identical to no filter at all. Shipping something here would mean
  shipping a stub, so nothing was shipped. Hebrew release hints in the parser
  and the `prefer_hebrew` ranking weight remain the honest version of this.
* The repository add-on cannot auto-update while `NofLevi/kodi` is private.
  Kodi fetches `repo/addons.xml` anonymously, so the URLs 404 until the
  repository is made public. Nothing in the code needs changing.

**Done since this list was written**

The Akamai entitlement flow, all five missing VOD extractors, Ktuvit, MDBList,
SeaDex, kids mode, the TMDb Helper player file and the repository URLs. Then,
overnight on 6-7 September 2026: debrid playback proven end to end, the whole
settings dialog made to render, lightweight made the shipped default, and the
run of defects recorded in `NIGHT-LOG.md` - which carries a per-feature
confirmation matrix saying how each one was actually checked.

Two settings that promised something with no code behind them were also
finished rather than deleted. `allow_uncached` was read by three debrid clients
and written by nothing, which made "cached only: off" a trap: the picker listed
sources the client then refused to open, so pressing play did nothing at all.
And `registry.forget_cache_status()` existed and was never called, so a torrent
that became cached still read as uncached for an hour.

## Attribution

The Israeli channel list and VOD catalogue were derived from the Idan Plus
add-on by Fishenzon (github.com/Fishenzon/repo), which is what the Kodi POV IL
build uses for Israeli content.
