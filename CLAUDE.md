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
2. **Server-side aggregators first.** Torrentio, TorrentsDB, Comet,
   MediaFusion and Zilean crawl on their own servers. One request returns
   dozens of parsed results. Local scrapers run only when they are relevant.
   Two of them ship on - Torrentio and TorrentsDB, which need no account and
   no configuration - and between them they find about a quarter more than
   either does alone. The rest need a config blob from their own web UI, so
   they are off rather than enabled and silently empty.
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
  Inside a **tab**, a row shows only what the rows above it have not already
  taken. That is not tidiness: the rows ask TMDB overlapping questions and
  always will - "trending this week" and "popular" are different questions
  with much the same answer, and measured against the live API they shared
  six films of twelve, which is what "too many duplications in topics" meant.
  Renaming rows cannot fix it. The mixed home listing is left alone, because
  it has no top-to-bottom order to inherit priority from. More is cached than
  is drawn so a row that loses half its page fills up again from what it
  already fetched, a row whose every item appears above it is shown as it was
  rather than emptied, and the claims are built once per tab because eighteen
  cache reads per row draw is a hundred milliseconds of nothing on a device
  with slow storage.
  The service warms them, so opening the add-on is a database read. Rows also
  belong to **sections** - Films, Series, Live TV - which are the tabs down the
  left of the home screen, and `SECTION_ORDER` is the running order of each
  one. That order is an editorial decision and is written out rather than
  implied by declaration order: a tab should open on what is new and popular,
  not on whatever was defined first. There is deliberately no "Home" tab; the
  `HOME` section id survives as "every row that is switched on", which is what
  the plain listing and the background service want and is a different
  question from "which tab am I looking at".
* `qr.py` is a QR encoder and a PNG writer in the standard library alone,
  used by the sign-in screen. It is here rather than fetched because an online
  QR service would be handed the authorisation URL, which is a live credential
  while it lasts, and `qrcode` needs Pillow.
* `ui/signin.py` is the one sign-in flow every service shares: scan a code,
  open a link, or type a key, offering only what the service actually has.
  Each debrid client declares `methods` and `key_url`; nothing else about
  signing in lives in the clients any more.
* `sources/aggregator.py` runs providers, merges by infohash, asks each debrid
  service once in batches which hashes are cached, then ranks. `model.dedupe`
  merges **twice**, and the second pass matters more than it sounds: an
  infohash catches three providers reporting one torrent, but not the same
  file re-uploaded as a different torrent, which is what the viewer is
  actually looking at. Measured on a real search, one file occupied positions
  1 to 8 for a Silo episode - the picker shows six rows, so it offered one
  option six times and called it six. The second pass keys on the release
  group with the size to the megabyte, the resolution and the codec, and an
  unnamed release joins a named group only when exactly one named group has
  its shape. That restriction is the safety: six different releases of that
  same episode share 4977 MB - LostFilm, EniaHD, an Italian one, a Spanish
  one - and collapsing there would hide every non-English version behind one
  row.
* `debrid/` has one class per service behind a common interface. The API
  quirks are documented where they matter: Real-Debrid has no bulk cache check
  any more, TorBox caps uncached adds at 60 an hour, Premiumize answers
  batches, AllDebrid has no bulk check either.
* `subs/` picks one subtitle: embedded track, then file hash, then release
  correlation, then AI translation of the best English match. Wizdom and
  SubSource are anonymous; Ktuvit is a members' site, so it is off until an
  account is entered and is asked after the faster sources.
* **Anime is named and numbered differently, and every layer had it wrong.**
  Two providers search by *name* rather than by IMDb id, and they were handed
  `original_title`, which for anime is Japanese in Japanese script - measured
  against Nyaa, that returns zero results for Doraemon, Reborn, Frieren and
  Madoka alike. `tmdb.english_title` supplies the name the trackers use. The
  episode number was season-relative, and fansub groups number absolutely, so
  season 8 episode 14 of Reborn is released as 203 - `tmdb.absolute_episode`
  counts it, `matches_episode` takes it, and the debrid file picker uses it to
  find the right file inside a batch. And nyaa never checked what came back,
  so a search for episode 14 returned forty-five results for episode *149*,
  because Nyaa matches the number as text. One subtlety worth keeping: a
  season marker changes what the number means, so "S3 - 11" is season three
  episode eleven and not absolute eleven.
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

1176 tests, all running against Kodi stubs, so no Kodi install is needed:

    python -m pytest tests

`tests/stubs/` is a small fake Kodi: `xbmc`, `xbmcgui`, `xbmcaddon`, `xbmcvfs`
and `xbmcplugin` in about 480 lines. It records what the add-on did rather than
drawing anything, so a handler can be run and then inspected. `conftest.py`
gives every test a fresh profile directory, empty settings and a clean cache,
plus a `no_network` fixture that fails loudly if a test reaches the internet.

| File | Tests | What it protects |
|---|---|---|
| `test_imports.py` | 91 | Imports every module. Kodi reports an import error as a blank screen, so this is the cheapest bug-catcher in the suite. Also fails on invalid escape sequences, stray control characters, and `"%s" % (a, b).strip()` - where the method binds to the tuple, not the string, which has shipped twice and once took the whole source picker down. |
| `test_addon_integrity.py` | 25 | What is invisible until Kodi loads the add-on: addon.xml validity, entry points and assets existing, every settings id having a default and matching it, skin XML parsing, textures existing, string files well formed, every localize id having a string, every provider setting having a module, every url_for naming a real route, every window class having its XML, the TMDb Helper player file naming only registered actions, and the four things a real Kodi taught us - settings labels being string ids, empty string defaults declaring allowempty, every setting the code uses being declared, and the row area holding a whole number of rows. It also fails on **a public function nothing calls**, which found twelve, two of which were checks somebody meant to make: "verbose logging" that did nothing, and a Hebrew-detector that never ran. And on **the home screen having fewer row controls than it has rows switched on**, which is how the Israeli live channels came to be enabled, warmed, cached and never once drawn. |
| `test_row_overlap.py` | 9 | A tab not showing the same posters three times under three headings. Priority running downwards so the row above keeps everything, a row above that is not warmed yet claiming nothing, a wholly redundant row shown rather than emptied, the plain listing untouched, and the memo being dropped when a row changes - which is the only way this can be wrong, and it shows as one repeated poster until the window is reopened. |
| `test_core.py` | 14 | The SQLite cache and the router: TTLs, compression, LRU eviction under the size cap, and url_for round-tripping through parse_params. |
| `test_routes.py` | 35 | Dispatches every route the way Kodi does, network blocked. Catches wiring mistakes that would otherwise show as an empty screen, stops a row that is known to be empty being offered as a menu entry that leads nowhere, and covers the paging a plain directory has to do with a "next page" entry because it has no scroll event to hang a fetch off. |
| `test_aggregator.py` | 23 | The orchestration the well-tested pieces hang off: top-K against "show all" (which used to return the same eight rows it was toggling away from), a debrid cache flag that must be able to come *down*, one batched question for a torrent three providers reported, and a provider that raises not taking the search with it. |
| `test_providers.py` | 17 | The Stremio adapter three of the four providers speak. Mostly about payloads that are not shaped the way the last one was: a size sent as a string or a float, a fileIdx that is not a number, and one unreadable stream costing only itself. |
| `test_anilist.py` | 10 | The anime catalog, and specifically that an outage upstream produces a hidden row and a log line rather than a broken screen. A failure is not cached as a result, so the row is retried rather than staying empty for the TTL. |
| `test_anime_numbering.py` | 33 | The three separate mistakes that made an anime episode unplayable, each found by surveying a thousand titles rather than by imagining it: the Japanese title searched against an index of romaji names, the season-relative number searched where an absolute one was needed, and nyaa not checking what came back. Plus the traps in reading a number off a name - a year sitting exactly where an episode number sits, a CRC, a version suffix, a batch range, and a season marker that makes the number season-relative. |
| `test_release_parser.py` | 45 | Resolution, source, codec, HDR, release group, season and episode, absolute anime numbering, Hebrew hints. Source ranking and subtitle matching both depend on it. |
| `test_sources.py` | 36 | Merging the same torrent from several providers, and the filter and ranking rules: resolution ceiling, disabled codecs, HDR, cam releases, implausible sizes, cached-only, and a cached source always beating an uncached one. |
| `test_seadex.py` | 15 | The anime exception: a curated pick beating a far more seeded release, matching by infohash so a lookalike can never be promoted, a cached source still winning, an uncovered title costing nothing, and a broken SeaDex not breaking the picker. |
| `test_debrid.py` | 11 | Picking the right file from a season pack, ignoring samples and extras, refusing to play the wrong episode, and a repeated cache question not becoming a repeated API call. |
| `test_torbox.py` | 23 | The one debrid service with a live account behind it, tested against the shapes it really returns rather than the documented ones - `checkcached` answering with a list of objects and omitting a miss, `requestdl` answering with a bare string. Also which call goes first: the account list is 466 KB and two to four seconds, `createtorrent` answers "Found Cached Torrent" in half a second, and a torrent TorBox is still downloading is not played at all. |
| `test_subtitle_matching.py` | 22 | Candidate scoring: hash match, identical release name, group, source, resolution, and the wrong episode pushed to the bottom. Plus the OpenSubtitles hash arithmetic. |
| `test_subtitle_sync.py` | 10 | The alignment engine: constant offset, PAL/NTSC drift, refusing to shift an unrelated subtitle, and a feature-length alignment staying inside its time budget. |
| `test_subtitle_chooser.py` | 24 | The hierarchy the viewer sees: embedded first, then exact, then estimates, with the label each earns. Forced tracks marked and skipped. |
| `test_subtitle_ai_ondemand.py` | 25 | Asking for a translation on purpose, and getting one where nothing exists. The row appearing over a perfectly good Hebrew match, because that judgement is the viewer's; the search widening past the two configured languages, because a film with no Hebrew and no English usually has a Spanish one; a translation never overwriting the subtitle it was made alongside; and the hand-off to the background service, which is where the work has to happen. |
| `test_subtitle_pipeline.py` | 16 | The whole decision end to end: only one file ever downloaded, a hash-matched reference re-timing a mismatched subtitle, translation falling back correctly, and partial translations reaching the player while the rest runs. |
| `test_ktuvit.py` | 19 | The only subtitle provider with an account: the password hashed on the wire, one login per day rather than per search, a stale session re-established exactly once, and the whole provider staying silent without credentials. |
| `test_translation.py` | 13 | The translator surviving a model that misbehaves: code fences, prose around the JSON, blank entries, chunks that fail and must be split. Timings must never move. |
| `test_translation_context.py` | 17 | Cast and gender reaching the prompt, and a gender-marking source language winning a close call without overriding a clearly better match. |
| `test_vod.py` | 30 | Israeli live TV and the catalogue: broadcaster ordering, referers carried through, relative paths given their CDN host, broken channels hidden, Hebrew substring search, and updating the bundled data invalidating the cache. |
| `test_entitlement.py` | 24 | The broadcaster ticket: one Akamai ticket covering every Keshet channel rather than one each, the on-demand CDN getting an AWS ticket instead because the two are not interchangeable, browsing never asking for one, a refusal leaving the URL unsigned rather than empty, and a failure never being cached. |
| `test_kan_mako.py` | 15 | An episode being a descendant of its programme, which is what stops the site's own navigation menu being listed as episodes, and Mako's on-demand streams being signed. |
| `test_reshet.py` | 19 | Reshet's numbering, which the broadcaster publishes wrongly. Season and episode come from the Hebrew title because the metadata fields disagree with it, and the API returns episodes unsorted. |
| `test_sport5.py` | 22 | Six megabytes of broadcaster JSON reduced before it is cached, a season's clips gathered into its programme, the manifest lifted out of the player URL, and the byte order mark that made the whole document unparseable. |
| `test_extractors_israeli.py` | 23 | Now 14, Sport 1 and 891FM, plus the check that every broadcaster in the catalogue has an extractor behind it. |
| `test_mdblist.py` | 19 | The list resolution staying bounded, the curator's order surviving lookups that finish out of order, a title TMDB does not know being dropped rather than blanked, and the API key staying out of the cache keys. |
| `test_kids.py` | 39 | Kids mode replacing the rows rather than filtering them, a pinned row order not being inherited, a warm cache not defeating it, the PIN being stored hashed and actually required to leave, and `catalog.peek` still saying None for a row that was never warmed. |
| `test_windows.py` | 55 | The home and search windows: rows filled lazily, the hero following focus, the on-screen keyboard opening on the script the interface is written in, suggestions never overwriting what was typed, entering the add-on landing in the Katan window, preloading past rows that come back empty, and typing surviving a Kodi whose Action has no getUnicode. Plus rows that grow as they are scrolled: one page for a row nobody touches, a ceiling for one they do, a page fetched off the GUI thread but never *added* off it, the cursor put back unconditionally rather than only when it looks like it moved, and a resting mouse pointer not paging through the catalogue on its own. |
| `test_details_window.py` | 19 | Information, seasons, episodes, back stepping out of the episode list before closing, and playing a show picking the next unwatched episode - walking on to the next season when one is finished, and never landing on the specials. |
| `test_sources_window.py` | 13 | The picker, which was crashing on every cached source before it had any tests at all. |
| `test_play.py` | 33 | From "the user pressed OK" to "Kodi has a URL": the autoplay decision, the service a cached source goes to, whether a download may be started, and - the one that took an evening to find - a resolved link that will not open being treated like any other source that will not play, with the dead CDN node remembered so the next source on it is free. |
| `test_qr.py` | 96 | The QR encoder, against the specification rather than against itself, because a QR code that is wrong looks exactly like a QR code and the only symptom is a phone that will not scan it. The block table has to add up to each version's codeword count, all thirty-two format strings have to match the published list, the Reed-Solomon coder has to reproduce the worked example in the standard, and every symbol is taken apart the way a scanner would - undoing the mask, the zigzag and the interleaving - and has to come back as what went in. |
| `test_signin.py` | 18 | The one sign-in screen: which methods a service offers and in what order, a service with one way in not being asked, and the three answers a poll can give - done, not yet, and never going to work, which is the one that stops a screen waiting out ten minutes. Also that mistyping a replacement key does not sign you out of a working account. |
| `test_profiles.py` | 26 | Every low-memory setting actually lowering load, all profiles setting the same keys so switching leaves nothing stale, **the shipped defaults being the lean profile key for key**, and the visual-polish switch raising artwork without ever lowering a richer profile. |
| `test_wizard.py` | 7 | The one setup step that is not an account: light against richer artwork, with what each costs, and a device that is told it has room rather than quietly switched. |
| `test_urlsession.py` | 13 | The standard-library HTTP session that replaces requests: parameters, form and JSON bodies, gzip, charsets, and an HTTP error being a response rather than an exception. |
| `test_upnext.py` | 8 | The next episode, including across a season boundary, and the signal being well formed. |
| `test_upgrade.py` | 27 | That an update costs the viewer nothing. Twenty-two credentials, and re-entering them on a projector with a remote is the difference between an update people accept and one they refuse. Reads the source for anything writing inside the add-on folder, which an update wipes; installs a real release over a real one and checks the keys, the subtitles and the profile survived; and refuses a truncated download, a file that is not a zip, and one with no `addon.xml`. |
| `test_failure_paths.py` | 24 | Somebody else's free service misbehaving. 429 retried and 404 not, `Retry-After` honoured rather than the backoff and capped so an hour-long wait cannot freeze a search, truncated JSON, DNS failure - and **the deadline**, which is the rule the whole add-on rests on. |
| `test_hebrew.py` | 21 | Hebrew is half the catalogue, not an edge case: cp1255 and iso-8859-8 subtitles, a byte order mark landing in the first cue, substring search over 2,810 Hebrew titles, a Hebrew title beside a Latin release group, and a filename that has to survive Android storage. |
| `test_packaging.py` | 5 | The built zip staying under 600 KB, containing no build junk, rooted at the add-on id, and carrying every file the add-on needs. |

Three of these catch whole classes of mistake rather than one bug:
`test_imports.py` finds anything that will not load, `test_addon_integrity.py`
finds settings and routes that promise something with no code behind them, and
`test_packaging.py` stops the add-on quietly growing.

## Releasing, and updating a device

One zip runs on Windows, the U4 and the Mi Box. `<platform>all</platform>`,
pure Python, no `.so` and no `.dll`, and the only required dependency is
`xbmc.python` - `requests` and `inputstream.adaptive` are both optional and
`urlsession.py` covers requests with the standard library. So there is nothing
to build per platform, and the whole problem is distribution.

    python tools/release.py            patch bump, news, build
    python tools/release.py --minor    0.1.4 -> 0.2.0
    python tools/release.py --dry-run  say what would change, write nothing

**Kodi only offers an update when the published version is higher than the
installed one.** The version sat at `0.1.0` through every change recorded in
this file, with `<news>0.1.0 - Initial skeleton.</news>`, so no device could
ever have been told there was anything new. `release.py` bumps both add-ons and
writes the news from the commit subjects, because a changelog nobody writes is
a changelog nobody reads.

### Where the files live

Kodi fetches a repository anonymously, so "private with a login" is not
possible and the only lever is discoverability. `NofLevi/kodi` stays private;
**Cloudflare Pages publishes only the `repo/` folder** at an unguessable
`pages.dev` address. Connect to Git, no build command, output directory `repo`.
`git push` republishes, so the release workflow does not change. Moving host
later is three URLs in `repository.katan/addon.xml`.

### On a device

Install `repository.katan` once from a zip, then Katan from within it; after
that Kodi updates itself. `tools/deploy_android.py` pushes the zips over adb,
which saves driving a file manager with a remote. Both Android boxes also need
**inputstream.adaptive from Kodi's own repository** - the DASH live channels
need it and it is not ours to ship.

### Updating from inside

`updater.py` is Tools -> Check for updates, plus an optional weekly background
check that only speaks when there is something to say. It exists alongside the
repository rather than instead of it, because the repository route needs the
repository installed and the first update after a zip install has nothing to
work with.

Keys survive because of where they are written, not because the updater is
careful: Kodi preserves `userdata/addon_data`, and **nothing here writes inside
the add-on folder** - every runtime path goes to `kodi.profile_path()`.
`test_upgrade.py` holds both halves of that, by reading the source for writes
into `addon_path()` and by installing a real release over a real one and
checking the keys are still there afterwards.

The one risky step is replacing the folder, so it is the one with care taken:
unpack to a temporary directory beside the add-on, refuse anything without a
parseable `addon.xml`, then swap and keep a backup until the swap succeeds. A
projector on wifi produces half-downloads, and installing one would leave an
add-on that cannot start.

## Integration testing

    python tools/drive_kodi.py        start Kodi, open every add-on path via
                                      JSON-RPC, run the device report
    python tools/check_channels.py    resolve every Israeli channel and ask
                                      the stream for a few bytes
    python tools/check_channels.py --update
                                      record what worked into channels.json

    python tools/survey_sources.py --count 1000
                                      ask a thousand titles how many sources
                                      and what subtitle accuracy they have
    python tools/survey_sources.py --report
                                      summarise it, and list the edge cases

`survey_sources.py` is the test that finds cases nobody thought of. The sample
is **stratified rather than random**, which is the whole point: a thousand
titles from "trending" would be a thousand recent English blockbusters, every
one of which works. The nineteen strata are each a way this has broken or
could - an anime episode numbered absolutely, an episode that aired three days
ago, a film nobody has seeded since 2009, a season-zero special, an Israeli
title. It writes one JSON line per title so a run can be stopped and resumed,
keeps its own cache so it never evicts what a real Kodi has warmed, and does
not touch the debrid services unless asked, because a thousand batched
requests is a poor way to treat somebody's account for a number that does not
depend on the answer.

It found the anime numbering defects below in its first forty titles.

`drive_kodi.py` is the test the unit suite cannot be: Files.GetDirectory on a
plugin path runs the plugin exactly as a user would, so a broken route shows up
as an empty or failed directory rather than as a silent blank screen.

Measured on Kodi 21.3, Windows, September 2026: nine paths opened, none failed,
no Python errors. Home 108 ms, live TV 118 ms, search 406 ms. The two checks
that fail are facts about this desktop rather than defects: the portable build
reports no hardware HEVC decode and ships without InputStream Adaptive. Kodi 21 ships
**Python 3.8**, not 3.11, so `int.bit_count` is unavailable and the popcount
fallback in `subs/sync.py` is load-bearing.

The device report on the shipped defaults: **about 6 MB of visible artwork**,
home from cache 13 ms, cache write 0 ms, subtitle alignment 378 ms, cache on
disk 2.1 MB. The zip is 424 KB against a 600 KB budget.

Home from cache used to read 1 ms and now reads 13 ms, and that is the
report's own variance rather than a change: rows now cache twenty items and
draw twelve, so the obvious suspect was the larger payload, and measuring it
directly puts both the twenty-item and the twelve-item version under a tenth
of a millisecond. The report reads a cold SQLite page cache once per run.

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

A later pass added two more, both about lists rather than layout:

* **`ControlList.addItems` and `selectItem` post thread messages; they do not
  act on the control.** So `getSelectedPosition()` called just after an append
  reads the state from *before* it. A row that grew while it was being
  scrolled therefore threw the viewer back to the start - measured at cursor
  56 to 0 on one append - and the obvious fix, "put the position back if it
  moved", never ran, because it asked a question whose answer had not arrived
  yet. Removing the condition fixes it: the messages are processed in the
  order they were posted, so Kodi's rebind lands first and the restore lands
  on top. The stub is synchronous, so it cannot show any of this.
* **`ACTION_MOUSE_MOVE` arrives while the pointer is merely resting**, and
  Kodi moves the selection on hover. A pointer left near the end of a row
  paged through the catalogue on its own: 94 items during a start-up with no
  input at all.

A later pass added two more, and both are about *where* code runs rather than
what it does. Neither is visible from Python and neither could fail a test.

* **Kodi's subtitle window is modal, and a modal silently refuses to let
  anything open over it.** The AI row asks for a Gemini key when there is
  none; the prompt was opened, Kodi declined, and nothing appeared - no
  error, no log line, just a press that did nothing. This is the same rule
  that stopped a context menu starting playback until the busy dialog was
  closed, met in a second place. Kodi also does not close the subtitle list
  when the plugin hands nothing back, and this row deliberately hands nothing
  back, so the list sat there over the thing it was writing to. The service
  closes it itself and waits until it has actually gone.
* **A plugin invocation is torn down the moment it returns**, so a thread
  started there is killed part way through anything slow. `player.py` already
  says this in its first paragraph - it is why the player monitor lives in the
  service - and the AI translation had to learn it again: a feature film is
  minutes of work, and the plugin process is gone in milliseconds. The dialog
  now leaves a window property and the background service, which outlives
  every window and every plugin call, picks it up within a second and does
  the work on a thread of its own.

Three more came from writing the edge-case tests, and all three had been
shipping. **The deadline did not work**: `run_parallel` used the executor as a
context manager, and leaving a `with` block calls `shutdown(wait=True)`, which
waits for every running task regardless of the timeout - so one provider that
hung for thirty seconds held the whole search for thirty seconds, which is the
exact failure bounded concurrency exists to prevent. Its own docstring already
claimed the behaviour it did not have. **The cache silently stopped storing
anything** after a schema change, because `CREATE TABLE IF NOT EXISTS` accepts
a table of the wrong shape and every "no such column" was swallowed on the way
past; there is no symptom except slowness. And **subtitle filenames carried
Hebrew onto the filesystem**, because `str.isalnum()` is true for Hebrew
letters, so the strip that was supposed to make them ASCII stripped nothing
from an Israeli title.

And one more about *where* code runs, which cost an evening and both wrong
answers before the right one. **A custom window must not be opened from inside
a directory call.** Both orderings fail, differently:

* Ending the directory *first* lets Kodi react to the failed `GetDirectory` by
  navigating to the previous window, and that Deactivate tears down the window
  that was opening - measured at 19 ms from Init to Deinit.
* Ending it *afterwards* holds `GetDirectory` open for as long as the window
  lives. Kodi sits behind a busy dialog the whole time, which the add-on then
  has to keep closing, and the plugin call is logged as `action home took
  1301141 ms` - twenty-one minutes. Press Exit and the stale directory
  completes, Kodi navigates, "stay in Katan" reopens it, and Kodi deadlocks
  during its own shutdown. Eight plugin invocations and a stuck process.

The window is therefore not opened from a directory call at all. The directory
is ended at once and `RunPlugin` asks Kodi to run the plugin again with no
directory attached; `handle` is -1 in that second invocation, which is how the
two are told apart. Start-up and "stay in Katan" call `RunPlugin` directly for
the same reason, rather than `ActivateWindow(Videos,...)` - the window is not a
directory, and routing through the video browser left an empty plugin folder in
the back stack. Measured after: one invocation, no busy dialog, and Quit exits
in 2.0 seconds.

The lesson worth keeping: the stubs can only be as right as our belief about
Kodi, and five of these were the stubs being more generous, or more
synchronous, than the real thing. Anything about how Kodi *renders*,
*validates* or *schedules* has to be checked in Kodi.

And one thing no screenshot could show. `catalog.enabled_rows()` returned more
rows than the home window had controls to draw, and both `prepare()` and
`onInit` silently sliced the list to fit. The Israeli live channels were past
the cut: enabled, warmed by the service, correct in the cache, listed by every
tool that asks the catalog, and never on the screen. The window now logs every
row it could not draw, and `test_addon_integrity` checks the constant against
the skin and against the rows that ship switched on.

And a whole class of bug that no log of ours would ever have shown. An episode
resolved perfectly, handed Kodi a well formed TorBox URL, and finished with
`action episode took 1366 ms` - success, as far as this add-on could tell.
Kodi's own log had the rest: `CCurlFile::Stat ... Timeout was reached`, and a
browser agreed. The CDN node accepted a TCP connection on 443 and then never
answered. **When something does not play, read Kodi's log and not only ours**:
the add-on's job ends at `setResolvedUrl`, and everything after that is
invisible from inside it. `play._reachable` now opens the link for one byte
before handing it over, so a dead node falls through to the next source
instead of producing a black screen.

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

A note on when a default actually applies, measured rather than assumed. Kodi
does **not** write a settings file until something is changed: a fresh install
has no `userdata/addon_data/plugin.video.katan/settings.xml` at all, and
`settings.DEFAULTS` is what the add-on runs on. Once a value has been written,
that file wins. So changing a default here reaches a new install immediately
and an existing one not at all — Tools → Performance profile applies the lean
set to an existing one.

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

The percentage is a probability that this subtitle fits this file, and it is
calibrated rather than a sum of whatever weights looked plausible:

    100   the same file by hash, or the same release name
     85   the same group, source and codec
     70   the right title, same source and resolution, a different group
     55   the right title and the same source
     40   the right title and nothing else
      0   demonstrably the wrong episode

That ladder is checked against live Wizdom results, not only fixtures. It used
to top out at **33** for a subtitle agreeing on source, resolution *and*
codec, because the largest piece of evidence was scored as nothing: every
candidate is the answer to a search for one specific title, and knowing that
was worth zero. So nothing a Hebrew provider returned could ever clear the
70% threshold, every film fell through to "below threshold, used anyway", and
a viewer reading 33 next to the best Hebrew subtitle in existence concluded
the add-on could not find subtitles - correctly, from what they were shown.
    AI translation   last, and not a subtitle anyone has - an offer to make
                     one. It is shown whether or not the list above it is
                     empty, because nothing here can tell a good Hebrew
                     subtitle from a bad one by looking at it, and the case
                     people complain about is subtitles that exist, are in
                     the right language, and are wrong. It appears without a
                     key configured and says so, because hiding it until
                     there is a key shows nothing at all to the one viewer
                     who most needs it. Switching AI off in the settings does
                     hide it - that is somebody saying they do not want it.

The row is honest about what it does not know: no percentage and a flat three
stars, because its accuracy is the accuracy of whatever it ends up
translating, which is not known until it has run. Its timings are not a guess
- they come from the source subtitle and are never touched.

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
  Everything around it *has* been run in a real Kodi 21 over a real playback:
  the row appears in Kodi's own subtitle dialog in Hebrew, pressing it reaches
  the background service in about 170 ms, the subtitle list closes itself and
  the key prompt opens over the still-playing film. What no key can prove is
  the only thing left - that the model returns usable Hebrew.
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
  issues", to any User-Agent including a browser one. Still refusing on
  8 September.

  This is no longer user-visible. `meta/anime.py` is a facade over two
  catalogues: AniList while it answers, because it carries the AniList id that
  SeaDex release rankings are keyed on, and **Kitsu** when it does not. Kitsu
  needs no key and its ids are the ones Torrentio already uses for anime, so a
  title found there arrives carrying the id the source layer needs and nothing
  has to be mapped. Measured with AniList down: the anime row fills with ten
  titles, search for "frieren" returns four, and the stream id resolves to
  `kitsu:46474:3`.

  The choice is made at most once every thirty minutes rather than per call,
  because otherwise every row and every search would pay for a failing request
  to the dead service first. A catalogue that dies mid-session falls through
  once and then re-decides. The one thing lost while on Kitsu is SeaDex
  rankings, which are keyed on AniList ids; that degrades quietly.

* **SubSource has moved behind a login.** The `POST /api/...` surface this
  add-on was written against answers 404, and the `/v1` REST API that replaced
  it answers 401 "Not authenticated" with no anonymous search route left. The
  provider therefore ships off in every profile, and says so in the log if it
  is switched on, rather than being a provider that is enabled and silently
  contributes nothing. That leaves Wizdom as the working Hebrew source;
  OpenSubtitles needs a key and Ktuvit needs an account.

**Missing features**

* **No Israeli torrent scraper, and there does not appear to be one to
  write.** This was investigated rather than assumed: the Israeli trackers are
  private and account-gated, Sdarot was dissolved in 2023, and Torrentio's
  `language=hebrew` priority was tested against the live service and returns
  results identical to no filter at all. Shipping something here would mean
  shipping a stub, so nothing was shipped. Hebrew release hints in the parser
  and the `prefer_hebrew` ranking weight remain the honest version of this.
* Auto-update needs the Cloudflare Pages project connected and the three URLs
  in `repository.katan/addon.xml` pointed at it. Until then the in-add-on
  updater reports no update rather than failing, and installing by zip works.

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
