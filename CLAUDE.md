# Pinky

A lightweight Netflix-style Kodi 21 add-on, built for weak hardware
(BYINTEK LOVE U4 projector, Mi Box), with Hebrew and English throughout.

## Layout

    plugin.video.pinky/       the add-on (three Kodi extension points, one codebase)
      main.py                 plugin entry
      service.py              background service
      subtitles.py            subtitle dialog provider
      resources/
        settings.xml          user settings
        data/                 bundled channel list and VOD catalogue
        language/             en_GB and he_IL strings
        players/              the TMDb Helper player file
        skins/default/1080i/  the custom windows
        lib/pinky/            all the Python
    repository.pinky/         so devices auto-update
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
7. **No skin fork, no runtime patching, no vendored add-ons.** Nothing here
   is carried that Kodi can fetch. `requests` is used when installed, and
   `urlsession.py` covers the same ground with the standard library when it
   is not, which keeps several megabytes out of memory on a small device.
   `inputstream.adaptive` is the one hard requirement, and requiring it is
   the opposite of vendoring: it is a *binary* add-on, so it could never sit
   inside a `<platform>all</platform>` zip, and declaring it lets Kodi
   install the right build for the box from its own repository.

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
  paste from a phone, or type a key, offering only what the service actually
  has. **All five accounts sign in by opening a link on a phone**, checked
  against the live services on 8 September 2026 rather than against our own
  comments - the comment saying TorBox had no device flow was out of date and
  had been for a while:

  | Service | Probe | Needs |
  |---|---|---|
  | Real-Debrid | 200 | nothing - `X245A4XAIBGVM` is published for open source apps |
  | AllDebrid | 200 | nothing - an agent name |
  | TorBox | 200 | nothing - an app name |
  | Trakt | 401 `invalid_client` | a registered application |
  | Premiumize | 400 `invalid_client` | a registered application |

  Two of them return a link that carries the code, so scanning it authorises
  the box with nothing typed at all: AllDebrid's `user_url` and Real-Debrid's
  `direct_verification_url`. The second one was being ignored in favour of
  `verification_url`, which is the same page with the work still to do.

  Pressing a service **shows every way in, and marks the easiest rather than
  taking it**. Running the device flow on its own was tried and is wrong: the
  alternatives become reachable only by backing out of a QR code, so somebody
  who wanted to paste a key they already had was cancelling out of a screen to
  find the option. Scanning still carries "(recommended)", which says the same
  thing without deciding it. Real-Debrid and Trakt show no list because they
  genuinely have one way in - the device flow mints their credentials, so
  there is no key anywhere that could be typed or pasted. There used to be a fourth entry, "open a link and type a
  code", and it was not a fourth way in at all: it ran the identical flow with
  the QR code not drawn, and the screen shows the link and the six digits
  either way. So it asked the viewer a question about themselves - do you have
  a phone camera - that the screen had already answered for both answers. It
  is gone, and the code is always drawn. Each client declares `methods` and `key_url`; nothing
  else about signing in lives in the clients any more. **Trakt goes through it
  too**, by declaring the same `label`, `methods`, `authorize` and `sign_out`
  a debrid client does - it is not a debrid service, but it is signed in to
  the same way, and having its own flow is what left it as the one account
  with no way to sign out. Every account is one button that opens a chooser;
  a connected one is asked whether to replace it or sign out, because a stale
  token makes every search quietly return nothing and clearing it is the fix.
  The raw id, secret and key fields still exist for anyone who wants them,
  at expert level, out of the way of the button.
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
  batches, AllDebrid has no bulk check either. TorBox's device flow was found
  by reading its OpenAPI document rather than its prose docs - `GET
  /user/auth/device/start` and `POST /user/auth/device/token` - and then
  called live, because that document publishes **no schema at all** for either
  success response. The one thing worth knowing before touching it: waiting
  and expired are *both* HTTP 400 and only the error name separates
  `DEVICE_CODE_NOT_USED` from `ITEM_NOT_FOUND`, so `post_json` cannot be used
  there - it turns every 400 into the default, and the body of the 400 is the
  entire answer.
* **There is no anonymous Hebrew subtitle source left that this add-on does
  not already use.** Asked to read DarkSubs and find what we are missing, and
  the answer turned out to be a map rather than a feature. Probed live on
  27 September 2026:

    | source | state | to us |
    |---|---|---|
    | Wizdom | 200 | already integrated |
    | Ktuvit | 200 | already integrated |
    | rest.opensubtitles.org | 200 | already integrated, anonymous |
    | OpenSubtitles.com | 403 without a key | **on, through DarkSubs' published keys - below** |
    | SubDL | 403 without a key | needs a key |
    | SubSource | wants `X-API-Key` | needs a key |
    | Yify Subtitles | 200, anonymous | **wired in - see below** |
    | ScrewZira | no DNS | gone |
    | Torec | 200, and it is a **parked domain** - every path answers a
      stub that redirects to `/lander` | gone |
    | Subscene | 403 | gone |
    | Podnadpisi | no DNS | gone |
    | BSPlayer | connection refused | gone, already recorded above |

  DarkSubs itself is no longer distributed - `mrgsi.github.io/gsource` answers
  404 - and the sources it listed were Ktuvit, Wizdom, Subscene,
  OpenSubtitles and BSPlayer: three we have and two that are dead. The open
  equivalent, a4kSubtitles, carries Addic7ed, BSPlayer, OpenSubtitles,
  Podnadpisi, SubDL and SubSource, and what it has that we do not is **two
  API keys**, not a technique.

  **Yify Subtitles was measured wrong here once, and the wrong number stayed
  in this file for a day.** The count above was `re.findall(r"flag-(\w+)", page)`
  against the search page, and that pattern does not match a subtitle row at
  all - every row's flag span is empty markup, `class="flag flag-"`, so what
  the regex actually found was a handful of unrelated page furniture (a
  language-picker dropdown in the site's own chrome) and reported it as
  "three Israeli rows for Inception" and "zero Hebrew" for three other films.
  Neither was a measurement of what the page held.

  The language a row actually states lives in a `sub-lang` span next to it,
  and casing is not consistent between pages - `english` on one, `Arabic` on
  another - which is exactly the kind of thing a case-sensitive scan misses
  silently. Read correctly: The Handmaiden carries **one** Hebrew subtitle,
  which the wrong count reported as none, and English alone runs into the
  hundreds for a popular film - Train to Busan 211, Parasite 218 - none of
  which the old number saw either.

  It is also 404-for-anime as stated, because it is film-only by construction
  - it is the subtitle half of the YTS torrent site, and YTS never releases a
  show. The obvious domain, `yifysubtitles.ch`, answers search cleanly and a
  plain client's first download attempt comes back 403 with `Cf-Mitigated:
  challenge` - which read as an unbeatable JS challenge and was not one:
  checked later against Kodi POV IL's own Yify source, a real browser
  `User-Agent` and a matching `Referer` reach the same file cleanly, 200, no
  JavaScript run anywhere. `yts-subs.com` needed none of that - measured
  identical counts for the same title on both, and this add-on's own default
  headers are enough - which is why it is what shipped, not because the
  primary domain turned out to be unreachable. It also turned out to need no
  extra request per candidate at all: its download URL is the search page's
  own slug, moved to a second subdomain with `.zip` appended, worked out once
  by decoding a base64 `data-link` attribute rather than fetching it.

  So it is wired in - `subs/providers/yify.py` - not for Hebrew, which is
  rare on it, but for the translation route: English and Arabic in volume,
  named to match a real YTS release rather than an uploader's own guess at
  one, which is what a large share of this add-on's own sources already are.
  Measured on Train to Busan: 236 candidates against opensubtitles_rest's 52
  in the same languages, and where a played release is itself a YTS one, a
  Yify subtitle for it scores 100, identical release name, by construction.

  So the ceiling is the corpus. Measured over 65 titles in four groups on the
  same day, the picker draws the best score any source-subtitle pairing can
  reach on **every one of them** - no gap anywhere - and the groups come out:

    group      n   native avg   at 100%   llm avg   no Hebrew at all
    film      20      95.0        19        98.3           0
    series    15      99.8        12        99.9           0
    foreign   15      97.7        12        86.6           0
    anime     15      31.2         2        97.9          10

  Anime's 31 is one fact and not many: ten of fifteen have no Hebrew subtitle
  in existence. **And no Hebrew is not a failure while there is something to
  translate**, which is the number that actually describes what a viewer
  gets. Counting the best route on offer rather than the Hebrew one, the same
  65 titles come out:

    group      n   best route avg   at 100%
    film      20        98.3          19
    series    15       100.0          15
    anime     15        97.9          12
    foreign   15        99.9          14

  **99.0 overall, 60 of 65 at 100%, 64 of 65 at 70% or better, and exactly
  one title below 70** - The Odyssey, four days old, whose only subtitles in
  any language were typed from a telesync. Anime moves from 31 to 98 on that
  reading, which is the whole argument for the LLM route existing.

  So the two levers left are both keys somebody has to make - an engine for
  the translation route, and OpenSubtitles.com for the search - and neither
  is a matter of code. The engine is the one that matters: it is what turns
  anime's 98 from an arithmetic into a subtitle.

* **A subtitle that came back from a search for this title is not
  necessarily for it.** `WEIGHT_TITLE` was granted to every candidate on
  exactly that reasoning, and the providers do not honour it. Asked for **The
  Odyssey (2026)**, the best Hebrew subtitle in the list was
  `Doctor.Odyssey.S01E18.The.Wave.Part.2` - a television series - scored 70%
  for agreeing on source and resolution, and the translation source was
  `The.Martian.2015`. Both drew as "Hebrew subtitle, 70% fit". The Doctor
  Odyssey one was then thrown out at playback for ending an hour before the
  film does, so the row promised Hebrew and the film played in English.

  `matcher._contradicts_title` takes only what a name *states*: an episode
  when what is playing is a film, which needs no second opinion; or a year
  nowhere near ours on a name that does not carry our title's words, which
  needs both halves. Either half alone destroys real subtitles - a year
  rejects **1917**, whose title is a year, and missing words rejects every
  translated title, `O Ultimo Tiro Certo` being One Last Shot. Silence stays
  silence: a name stating neither is left to the evidence below it.

  Swept over 17 titles and **526 Hebrew candidates: 12 rejected, all 12
  genuinely for other shows**, and zero false positives - Blade Runner 2049
  kept 37 and 1917 kept 30.

* **Kodi cannot read a subtitle that does not say what it is.** Three files
  were checked byte for byte and were correct UTF-8 Hebrew, and the screen
  showed mojibake anyway. `locale.charset` is `DEFAULT`, which means *detect
  it*, and there was nothing to detect: every Hebrew letter is 0xD7 and a
  second byte, and 0xD7 alone is a perfectly good character in cp1252 and
  cp1255, so it decoded one byte at a time. `srt.write` uses `utf-8-sig`.
  Three bytes end the guessing, and `decode` has read the mark first since it
  was written.

* **Kodi lays every subtitle line out left to right, and every translation
  made here was showing its punctuation backwards.** A Hebrew line written
  correctly - "אנחנו כאן." - shows its period before the first word, and a
  dialogue dash at the end. Checked in Kodi 21 on one clip four ways: as
  written (wrong), pre-flipped (right), wrapped in RLE..PDF (right), behind
  an RLM (wrong). Uploaders know: of about 260 Hebrew downloads from all
  five providers, some 240 put the end punctuation first and are right on
  screen as they are. What is written correctly - a few uploads, and every
  AI translation, which is the route anime depends on - was wrong.
  `srt.laid_out_for_kodi`, in `srt.write`, leaves a flipped file alone and
  wraps each Hebrew line of any other in a right-to-left embedding, which is
  also what Kodi POV IL settled on on devices. Checked in Kodi after, from a
  file `srt.write` made. The stubs cannot see any of it.

* **The picker is ten native, ten AI, five English - a ladder, not a
  scoreboard.** Drawing everything that passed the filters was tried: 118
  rows on Toy Story 5, with the three lists that are the whole point of the
  page buried inside them. Sorting the page by fit was tried too, and put
  three LLM rows at 76% above seven NATIVE rows at 70%, which reads as
  "translate this" on a film that has a Hebrew subtitle somebody made. Hebrew
  beats a translation beats English whatever the fits say; the fit orders the
  rungs and does not reorder the ladder. Deduplicating the rows was also
  tried and is the worst of the three - the three lists are *supposed* to
  name one release once per route it has, and collapsing them left every row
  on Hikaru no Go reading LLM while English subtitles fitting at 100% sat in
  a list that had been thrown away.

* **A film with no home release yet has nothing but cams, whatever they are
  called.** Spider-Man: Brand New Day, two months into its cinema run, kept
  fifteen releases after the cam filter took thirty-four: "D.WEBRip",
  "1TamilBlasters HQ HD", "little blur but good rip", "kinda like webrip
  next best thing no ads". The filter reads what a name *says*; these say
  nothing. `scoring._only_in_cinemas` asks the film instead - a cinema date
  within 180 days and no digital, disc or television release anywhere in
  TMDB's release dates, which `tmdb.movie` already fetched and now keeps as
  `extra["cinema"]` and `extra["home"]` - and then every copy is a cam, under
  the same `allow_cam` switch. Measured: Brand New Day 15 -> 0, The Odyssey
  19 -> 0, Hope 0, and Toy Story 5, Supergirl, Project Hail Mary and six
  more already out at home untouched. The Odyssey found a second bug on the
  way: its copies failed the resolution floor first, the floor stood aside
  with too few left, and the second pass repeated the checks by hand without
  the cinema rule. Both passes now share one `why`.

  The same release dates answer a second question: nothing is released
  before its first showing anywhere, premieres included (`extra
  ["first_shown"]`). Anaconda (2025) kept four copies of "Anaconda.2024" -
  the Chinese film of that name - which the one-year tolerance let through,
  because a film's year may run *late* (Obsession premiered in 2025, opened
  in 2026). `scoring._before_it_existed` refuses only an *earlier* year.

* **A film still in cinemas is uploaded under words the cam filter had never
  heard.** The table knew `cam`, `ts`, `tc` and `telecine`; the names in the
  wild are `PREHD`, `HQ Pre`, `HDTC` and `LiNE`, all of which parsed as
  `unknown` and walked past a filter that was switched on. They are checked
  *before* `web`, because one upload claims both - `1080p.D.WEBRip` and
  `720p_V4_HDTC_Multi_LiNE` are the same file and the second half is the true
  one. LiNE is line audio and is matched only beside a language or quality
  word, because The Thin Red Line is a film. Measured: Spider-Man: Brand New
  Day drops 35 of 55 rather than 32, The Odyssey 16 of 38 rather than 15.

  The same search also offered `Marvel Studios Iron Man 2008`.
  `_another_production` cannot catch that - it reads the year directly after
  the title and that name does not begin with the title at all - so
  `scoring._a_different_film` asks the same two-signal question the subtitle
  matcher does.

* **A provider that cannot answer must not be asked.** `deadline hit after
  10.0s, dropped: ktuvit` was not about Ktuvit: comet, mediafusion, zilean
  and external were all switched on with no configuration between them, and
  their whole answer is the blob their own web UI produces. Probed live,
  Comet answers **403** and MediaFusion **200 with zero streams** - each up
  to nine seconds on a pool of two workers. A config-only provider with no
  config is refused before the request and says so once.

* **A 429 was bought again on every search.** TorrentsDB refuses in 0.15s
  with no `Retry-After`, so the cost was never the request - it was the 0.6
  second sleep the retry paid before being refused again. A host whose retry
  is also refused is left alone for thirty seconds; the same search then runs
  1.53s and 0.21s and returns the same eighty sources either way. Thirty and
  not longer, because TorrentsDB finds about a quarter more than Torrentio
  alone. It is throttling rather than gone: its manifest answers 200 and says
  `configurationRequired: false`.

* **What the other multi-source add-ons have that this does not is two API
  keys, not a technique.** a4kSubtitles - which is open, where DarkSubs is
  not - carries Addic7ed, BSPlayer, OpenSubtitles, Podnadpisi, SubDL and
  SubSource. SubDL answers **403** anonymously and SubSource now wants
  `X-API-Key`, which is this file's own note about SubSource going behind a
  login, confirmed from the other side.

* **An IMDb id that finds nothing is not the same as there being nothing.**
  This is the one to carry elsewhere. `rest.opensubtitles.org` files an upload
  against an id only if whoever uploaded it said so, and for a long-running
  anime most of them did not: `imdbid-0426711` knows English for Hikaru no Go
  episodes 1 to 30 and stops, while the same season and episode asked *by
  name* returns `Hikaru No Go S01E32.en.vtt`. The numbering was already right
  - `common.episode_numberings` asks at 2x02 and at 1x32 both - and both
  addresses returned nothing, because the address was never the problem.

  Then the answer was thrown away. That row's `SeriesIMDBParent` is
  **13364846**; the show has at least three IMDb entries (0426711, 0303461,
  13364846) and an uploader files against whichever they were looking at. The
  mis-registered-hash guard rejected it - and having gone around the id
  *because it found nothing*, discarding the answer for disagreeing with that
  same id is the search cancelling itself out. `_imdb_agrees` is therefore not
  asked of a name query; the name and the episode number are what constrained
  it, and the wrong-episode guard downstream is what checks it.

  The name query runs **only after the id query comes back empty**, so it
  costs one request in exactly the case that currently returns nothing and
  none at all in the common one. It asks under the *show's* name, because
  `title` on an episode is the episode's own name and nobody files a subtitle
  under "The Last Day of the Preliminaries".

  Measured: Hikaru no Go season two went from **0 of 8 episodes with an
  English subtitle to 6 of 8**. Swept over nine titles across anime, drama and
  film afterwards, counting rows the matcher scores at zero: **no wrong-episode
  contamination anywhere**. Two remain empty - Bleach TYBW 2x46 and Kurulus
  Osman 1x01 - and OpenSubtitles has nothing for either in any language, which
  is a gap upstream rather than here.

  **The name query was only ever the English one, and asked "why the Yify
  regex" turned into asking whether that was still true.** `_by_name` read
  `title`/`show_title` alone, and for anime that name is not the only one a
  release is filed under - a fansub and a raw group both routinely use the
  Japanese romaji title instead. Asked for Naruto Shippuden 1x01 in English,
  `"naruto shippuden"` returned 12 rows and `"naruto shippuuden"` 2, and one
  of those two - a HorribleSubs upload - was not in the first list at all.
  This index matches against its own title mapping rather than a filename,
  so the two spellings do not resolve to identically the same rows, and
  asking only one leaves a real subtitle unreachable the same way an unasked
  IMDb entry did above. `meta["aliases"]` - the same romaji titles
  `_a_different_series` now reads, filled by `tmdb.romaji_titles` - is asked
  too, capped at two so a title with several regional respellings costs at
  most three requests rather than one per alias, and only in the case that
  already costs one: the id query already failed.

  It also stopped preferring the wrong title outright. `title` and
  `show_title` follow the UI language - `tmdb.language()` returns whatever
  `ui.language` resolves to - so a Hebrew household's `meta["title"]` is a
  Hebrew string, and a Hebrew-script query to this same index returns
  nothing, for the reason given two sections up: the index is Latin-only in
  practice. `search_title` is the anime-specific English name computed for
  the tracker search and was sitting unread by this file; it is now asked
  first, ahead of a title that might not be answerable at all.

  A series that is not anime now has `english_title` too, from TMDB's
  translations (see the three engines below), and it is asked ahead of
  `show_title` for the same reason. Films still ask under `title`: their id
  query almost never comes back empty, and they were left alone on purpose.

  What it looked like from the outside is worth recording, because it is why
  this went unnoticed: the picker drew eight sources all reading
  `LLM translated from JA, 52% fit`. Eight rows, one file - the *same* lone
  Japanese subtitle rated against eight releases, printed eight times and
  called a choice. A picker that offers one option eight times looks like a
  picker that is working.
* **The best subtitle for a release is the one that shipped with it, and it
  was in the torrent the whole time.** Measured on Hikaru no Go 2x03, the
  source the picker had been offering all week:

      Hikaru.No.Go.TV.EP33.BluRay.1080p.AC3.x264-CHD.mkv     the video
      Hikaru.No.Go.TV.EP33.BluRay.1080p.AC3.x264-CHD.srt     25 KB, 356 cues

  One hundred and fifty-two files in that torrent, seventy-six of them
  subtitles, one per episode, named to match. Nothing here had ever looked.

  It matters more than one more provider. A file that ships with the release
  was typed against that exact cut, so it is in time **by construction** - no
  hash, no cross-language proof, no correlation. That is the whole of the sync
  problem for anime, where a hash almost never exists and the cross-language
  proof is usually one language short, solved by not needing a ruler at all.
  It is an ideal translation source for the same reason, because a translation
  inherits its source's timing.

  The matcher needs telling nothing. The subtitle's name *is* the release
  name, so `_same_name` answers "identical release name" and 100, which is
  what it deserves. Only TorBox can list the files inside a torrent, so
  `registry.client_with_sidecars` asks rather than assumes, and only the file
  chosen costs a `requestdl`: a season pack has seventy-six and the viewer
  wants one.

  Language comes from the filename where the release says - `.he.srt`,
  `.eng.srt` - and is assumed English otherwise, which is what an unlabelled
  subtitle beside a fansub release almost always is. The guess is safe
  because `download_candidate` refuses cues whose script is not the language
  asked for. A dual-language fansub file survives that check: measured on the
  Hikaru file, whose cues carry English and Japanese on two lines,
  `detect_script` answers `en`.

* **Searching by a title's native name finds nothing**, tested rather than
  assumed, because it looks like it ought to work. `rest.opensubtitles.org`
  answers a query in Japanese or Hebrew script with zero rows where the same
  query in English returns three, a hundred and a hundred: Hikaru no Go,
  Attack on Titan and Naruto all return nothing under their Japanese names,
  and Fauda, Tehran and two Israeli films nothing under their Hebrew ones.
  Bleach is the only apparent exception and only because "BLEACH" is already
  Latin. The index is Latin-only in practice, the same way the trackers are.

* `subs/` picks one subtitle: embedded track, then file hash, then release
  correlation, then AI translation of the best English match. Wizdom and
  SubSource are anonymous; Ktuvit is a members' site, so it is off until an
  account is entered and is asked after the faster sources. Ktuvit is entered
  through **one button in the subtitle settings at normal level**, not the
  expert toggle and two fields it used to be: it offers the free signup page
  as a scannable code, asks for the email and password, switches the provider
  on and *tries the login while somebody is still looking at the screen*,
  because a stored-but-wrong password otherwise surfaces days later as a film
  playing with nothing. It cannot ship with a credential of its own - one
  login shared by every installation is what closes an account, and the dead
  credential would then be frozen into every installed copy until a release
  replaced it.

  **There is no Hebrew subtitle for what this household watches.** This is
  the most important measurement in this file and it was never taken: every
  earlier accuracy run was stratified for *source-finding* edge cases and was
  heavily English, which is exactly the sample that cannot answer the
  question. Asked directly on 24 September 2026 - seven titles each of
  Turkish drama, Korean drama, anime and Israeli television, both Hebrew
  providers, search only:

    group      titles   any Hebrew
    turkish        7        0
    korean         7        0
    anime          7        0
    israeli        7        2

  Zero of twenty-one. Both providers were checked against titles that
  certainly have Hebrew on the same run - Fight Club 26 and 24, Breaking Bad
  9 and 7 - so this is the corpus and not the code.

  **Which is not only anime.** Asked as two questions - is there a Hebrew
  subtitle, and failing that is there one in a language the model can work
  from - over seven titles a group:

    group        has Hebrew   translatable   neither
    anime            0/7          2/7           5
    turkish          0/7          2/7           5
    korean           0/7          3/7           4
    us series        4/7          4/7           3
    israeli          3/7          3/7           4
    films            6/7          7/7           0

  Films are strong and anime and Turkish drama are equally weak, with Korean
  behind them. The sample is *currently popular* titles, which skews hard
  towards episodes that aired days ago and nobody has subtitled yet, so a
  finished season does better than this - but the ordering between groups is
  the point and it does not depend on that.

  **The "neither" column overstates it, and the reason is worth knowing: none
  of this can see inside the file.** `embedded.py` asks Kodi, so it answers
  only during playback and no survey has ever counted a track. Read straight
  out of the Matroska header over two ranged requests - the same trick the
  hasher uses to avoid downloading a film - **five of eight anime episodes
  carry a subtitle track**, `S_TEXT/UTF8` or `S_TEXT/ASS`, which is what
  fansub releases are. A track inside the file is in time by construction,
  so for anime the honest reading is that the automatic path often does find
  a subtitle where every number here says it found nothing.

  What that leaves as the real gap is the *picker*, which cannot promise a
  track it has no way to see and so offers eight LLM rows next to a file that
  already has subtitles in it.

  What follows from it is the whole shape of the subtitle problem here. For
  anime and foreign drama, AI translation is not a fallback and not a
  second-best: it is the **only** route, and the quality of the translation
  source is the quality of the subtitle. It also disposes of any remaining
  argument about Ktuvit adding reach - on a Hebrew-weighted sample it adds
  neither, because there is nothing there to add.

  And it is why an engine that is out of quota is not a degraded experience
  but a blank screen, which is what `translator.engines` exists for.

  **What the automatic path is actually worth, measured 23 September 2026**
  over 223 stratified titles with real debrid streams, against two rulers: a
  hash-matched subtitle where one exists (ground truth) and the best candidate
  from a *different* provider (corroboration). Of the subtitles it applied,
  100% were in the language claimed, 100% covered the whole runtime, 100% of
  the hash-referenced ones fitted well, and 78% of 80 were corroborated to
  within a second. Where a title had both rulers they agreed 7 times in 8,
  which is what makes the wide number worth quoting. So **it is reliable at
  "is this the right file" and blind to "is the Hebrew any good"** - nothing
  in the pipeline reads the translation, and the case people complain about
  is a subtitle that exists, is in the right language and is wrong. That is
  why the manual chooser is not redundant and must stay.

  Three defects it found, none of which any log would have shown, because all
  three end at "nothing usable" next to dozens of candidates:
  * **A download that returned nothing cost a budget slot.** The budget of
    three is a memory decision - parse a few subtitles on a small device, not
    fifty - and an empty response costs no memory at all. When
    rest.opensubtitles.org began refusing downloads part way through the run,
    three empty fetches spent the allowance and everything behind them was
    unreachable: Pulp Fiction played with no subtitle while 45 working Wizdom
    candidates sat unasked, and so did Titanic, Taxi Driver, The Lion King,
    The Terminator and Apocalypse Now. It also silently disabled the hash
    reference, which is the only thing that can verify timing. Failures are
    correlated by provider rather than spread evenly, and a flat ceiling
    cannot express that - OpenSubtitles ranks eleven rows above Wizdom's first
    on Pulp Fiction - so a provider is written off after two *empty* answers
    and its remaining rows skipped for free. An archive that arrives and will
    not parse is the upload's fault and never counts against the provider.
  * **An archive naming no language at all was refused as ambiguous.** Wizdom
    is Hebrew-only and has no reason to tag anything, and its Pulp Fiction zip
    holds the same subtitle under two release names. There is no language
    present to be confused with, and the guard that actually works is
    downstream and unconditional: `download_candidate` refuses cues whose
    script is not the language asked for.
  * **One partial file ended the search.** `verify_and_sync` has always said
    "the caller's fallback to the next candidate handles both", and for the
    Hebrew and English steps that fallback did not exist. Harry Potter 2 had
    fourteen Hebrew subtitles, the best-ranked was a 710-cue CD1 half, and the
    film played with none while thirteen whole ones sat behind it.

  Eleven of the thirteen recoverable titles came back. The two that did not
  are the case where the top three candidates are *all* CD1 halves, which the
  three-download budget cannot outlast and which is not worth more downloads
  on every playback to rescue.

  **Ktuvit was signed in to for the first time on 23 September 2026, and
  almost nothing about it was right.** It had been written from a guess at the
  site rather than from the site, shipped off by default, and so never
  contradicted. Four independent faults, each fatal on its own:

  * the host was `members.ktuvit.me`, retired and now **NXDOMAIN** - every
    request died at DNS, so no credential could ever have worked. The same
    paths answer on the apex `ktuvit.me`;
  * the password was sent as a **SHA-1 digest**, which the site has never
    wanted. It wants its own `Encrypt(email, password)`, out of
    `js/Modules/LoginHandler.js`: PBKDF2 (salt as the password, email as the
    salt, SHA-1, 3000 iterations) to a 128-bit key, AES-CBC over the password
    with an IV that is `CryptoJS.enc.Hex.parse(email)` - nonsense on an email
    address, which is the point, and reproducing the nonsense exactly is the
    job - then SHA-256 of the ciphertext, base64 of that. The keying salt is
    one `var` in the homepage and is scraped per login, because the site can
    rotate it;
  * the session cookie was taken as `ASP.NET_SessionId`, which Ktuvit hands
    to **anonymous visitors**. So a refused login still produced a cookie,
    cached for a day, used to fetch logged-out pages - a configured provider
    silently finding nothing. The real one is `Login=u=<hex>&g=<hex>`;
  * both subtitle-row regexes matched markup that does not exist. A row is
    six plain cells: the release name in a `div` before the `<br>`, the id on
    two `data-subtitle-id` links in the last cell.

  And a fifth that produces a *wrong* episode rather than none:
  `MovieInfo.aspx` accepts `&season=&episode=` and **ignores them**, so a
  series asked that way gets season one whatever was wanted. Episodes have
  their own fragment endpoint, `GetModuleAjax.ashx?moduleName=SubtitlesList`.

  Two things about the shape of it are worth carrying elsewhere. The auth
  boundary is not where it looks: search, the episode list and even issuing a
  download identifier all work anonymously, so `IsSuccess: true` on the
  identifier says nothing about the session - the refusal lands on the file
  fetch, as **HTTP 200** with a sentence of Hebrew where a subtitle should be.
  And `SearchPage_search` answers `IsSuccess: false` **on success**, so
  nothing may gate on it.

  Measured after: 66 Hebrew subtitles for Pulp Fiction, 1806 cues downloaded
  and decoded, 7 for Breaking Bad 1x01 through the other endpoint.

  Why none of it was caught: the provider ships **off**, so no unit test
  reaches the network and nobody without an account ever saw a log line. It
  was invisible by construction, and "implemented and fixture tested" meant
  only that it matched what we had imagined. `tools/e2e.py` now probes the
  host with an *empty* login, which needs no account and is the gentlest
  thing to ask of somebody else's server, and checks the salt is still
  published - the one runtime-scraped value no fixture could ever cover.

  **What it is worth, measured over 200 titles the day it started working.**
  Ktuvit answered on 66 of them and offered 1081 candidates - more than
  Wizdom's 908 - and it was the **only** provider on none of them and won
  **two**: Practical Magic on an exact release name nobody else had, and
  Palmer. So it adds depth, not reach: every title it could help with was
  already covered by an anonymous source, and `below threshold, used anyway`
  is still the largest route at 28 of 85, unchanged by its presence.

  It stays on that evidence rather than in spite of it - the two it won were
  score-100 exact matches, which is the *confident* kind of win, and it costs
  nothing at all to anyone who does not configure it. But it must not be sold
  as the answer to the weak tail, which is what it was assumed to be.

  Two honest caveats on those figures. The corroboration in the same run rose
  from 78% to 94%, and that is almost certainly the three download fixes
  rather than Ktuvit - the chosen subtitle simply got better, so independent
  uploaders agree with it more often. And the sample is stratified for
  *source-finding* edge cases and is heavily English-language; the case Ktuvit
  exists for is a Hebrew household watching Turkish drama and anime, where
  Wizdom is thinnest, and that is under-represented. A Hebrew-weighted run
  would be a fairer test and has not been done.

  `utils/aes.py` exists for that login and nothing else: Kodi 21 is Python
  3.8, whose standard library has PBKDF2 and SHA-256 but no block cipher, and
  `pycryptodome` is compiled and could never sit in a `<platform>all</platform>`
  zip. Encryption only, about 140 lines, checked against FIPS-197 and NIST
  SP 800-38A rather than against itself - the same argument as `qr.py`.

  **Anime subtitles from Jimaku were built, measured and not shipped.** Over
  a thousand anime shows, Jimaku had about 58% of episodes (Kitsunekko 5% as
  a single file; AnimeTosho's extracted tracks were better still and the
  service closes in October 2026), and for about one episode in six it was
  the only translatable subtitle. It was dropped because that sixth only
  matters with AI translation on, Japanese is the weakest source for Hebrew
  (it drops the subject, so gender is guessed), and it cost a provider, an id
  mapping and a key the viewer has to make. What finding it needed, for next
  time: Jimaku is keyed by **AniList id, one per cour** - searching by name
  missed Naruto Shippuden and Symphogear, which it had - and ARM
  (arm.haglund.dev) maps a TMDB show to AniList per TMDB season but carries
  no episode offset, which a Kitsu episode-count walk or Fribb/anime-lists'
  `episode_offset` supplies. Two fixes it forced are general and stayed, in
  `release.matches_episode`: `S01E66` is the 66th episode counted from the
  first, which is how Hikaru no Go's 3x06 is filed, and a stated season has
  to agree, so "Oshi no Ko S3 - 06" is not season one's episode six.

  **The file hash pays off 10% of the time and is worth keeping anyway**,
  which is not obvious and was nearly dropped for it. Measured over 147 titles
  with candidates, fifteen had a hash match: five where the match was itself in
  the wanted language, so the viewer got a subtitle that is right by
  construction, and ten where it was in another language and therefore the
  only *ruler* this add-on owns. Four of those ten caught a name-matched
  subtitle that did not fit at all - fits of 0.12 to 0.27 - which were then
  left at their original timing rather than confidently shifted somewhere
  worse. Nothing else can catch that: `reference_cues` accepts a hash match
  and nothing else, so with no hash `sync.py` never runs and the release name
  is the whole judgement.

  What it is *not* worth is being waited for. It is a HEAD and two 64 KB
  ranged requests against the debrid CDN, about a second, and it used to run
  to completion before the first provider was asked - a second of an
  already-playing film with no subtitles on it. `video_hash_later` starts it
  and hands back a getter; the nine providers that never look at a hash are
  asking while it is still being read, and only the three that need it wait.
  It is one thread rather than a task in the search pool, because a task that
  waits inside the pool for another task in the same pool deadlocks as soon as
  every worker is a waiter.

  **The wait was five seconds and the hash took 5317 ms.** Measured on the
  same file twice: 4588 ms once, 5317 ms the next time, so five sat exactly
  on the boundary and the second run missed it by 317 milliseconds. That is
  not three providers short of an answer - without the hash nothing can come
  back marked `hash`, so there is no timing reference for *anything* and the
  release name becomes the whole judgement, which is the paragraph above
  describing the failure rather than the design. Eight seconds now, which
  costs nothing when the hash is quick because the wait ends the moment it
  lands, and the other nine providers are being asked throughout.

  **Two things were switching the timing checks off, and both were mine.**

  `consensus.wanted` skips verification when the top score is "decisive", and
  decisive was **90**. Raising `WEIGHT_EPISODE` to 30 moved scores up, so
  combinations that are not identity matches began clearing it: measured over
  twenty titles, six scored 90-99 with no identity match at all and skipped
  the only check available without a hash, on reasons like "source" and
  "resolution" - which is the ladder's *70* rung. Mushoku Tensei reached 91
  as 85 plus a **download-count nudge**, and how many people downloaded a
  subtitle says nothing about whether it is in time.

  Decisive now means 100, which is exactly identity: `matcher.rate` returns
  100 for a hash and for an identical release name and caps everything
  additive at `ADDITIVE_CEILING`, 99. A test pins the two constants together,
  because if they ever cross again this silently stops meaning anything. All
  six of those titles had a partner to corroborate against, so the cost is
  bounded and was measured before it was spent.

  `TIMING_EVIDENCE_LANGUAGES` was `("en", "es")` and only the languages
  *missing* from `subs.languages` were asked - which for he,en is Spanish,
  one language. `consensus.timeline_reference` needs **two** agreeing
  non-target languages before it calls a timeline proved, so the
  cross-language proof could never run: one short by construction, and
  `missing[:1]` made sure of it. It is now the five with measured coverage,
  Arabic first, three asked in parallel under the same disposable deadline.

  **And for anime none of that was ever going to help, because the thing
  whose timing matters there is not a Hebrew subtitle.** There is no Hebrew
  subtitle. What reaches the screen is a *translation* of an Arabic or
  English file, so the timing the viewer sees is that file's timing - and
  `hash_reference` accepted a hash and nothing else, which for anime is
  never. `consensus` has been able to prove a timeline without a hash since
  it was written, and was only ever asked about the *target* language, which
  for anime has no candidates at all, so it was never asked.

  `translation_reference` asks it about the file being translated instead:
  the hash first, then two independent languages that agree. Measured on four
  anime episodes, where hash reach is effectively zero - Mushoku Tensei and
  Frieren both got a reference, and Frieren's source was **1.10 s out** and
  was corrected. Two of four, where it had been none of four.

  **A translation inherits its source's timing, and nothing re-read it.**
  Both translation paths said in so many words that timings come from the
  source subtitle and are never touched, and that was the whole of it: a
  Polish subtitle matched on release name became a well-gendered Hebrew
  subtitle that was out by however much the Polish one was out. The Hebrew
  path has re-timed against a hash reference since the beginning; the AI path
  simply never asked for one. `retimed_for_translation` runs the source
  through the same aligner first. It only ever *shifts* - `verify_and_sync`
  may reject what a reference disproves, and that is right when another
  Hebrew candidate waits behind it, but here the source is chosen and the
  budget spent, so refusing would leave the viewer with nothing instead of
  something a second out. `reference_cues` also takes a `skip` now, because
  this path asks about the same languages it translates from and a file was
  one call away from being its own ruler.
* **Anime publishes the same episode twice** - Japanese audio with subtitles,
  and an English dub - and the release name is the only place that says which.
  Two rows of the same resolution, size and group were indistinguishable in
  the picker, so choosing the dub meant starting one and backing out.
  `release.parse` reads it into a `dub` field and the picker label says DUAL,
  DUB or SUB. It is deliberately blank rather than wrong for everything else:
  "MULTI.SUBS" on a live-action film is a claim about its subtitles and says
  nothing at all about the audio, and `[SubsPlease]` and `[HorribleSubs]` are
  group names that happen to contain the word.
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
* **An anime episode has two addresses, and TMDB gives us the one nobody
  indexes.** TMDB folds a whole multi-year arc into one season - all of
  Bleach's Thousand-Year Blood War is "season 2", numbered 1 to 50 - while
  Kitsu, AniDB and every release group treat each cour as its own series
  numbered from 1. So every provider keyed on an id was asked for
  `tt0434665:2:46`, which returns **nothing at all** from Torrentio, when the
  same episode as `kitsu:49444:6` returns nine sources. `kitsu.episode_address`
  walks Kitsu's own cour lengths - 46 - 13 - 13 - 14 = 6, the fourth cour -
  and the aggregator asks under both addresses.

  Three things that had to be got right, each of which produces a *wrong*
  episode rather than none, which no filter downstream can catch because the
  file is exactly what it says it is. A one-episode recap sits between the
  second and third cours and shifts everything after it, so only a broadcast
  run of four or more episodes counts as a cour. The season's own name is
  required, because a text search for "Bleach" alone returns the 366-episode
  original, six specials and several unrelated shows. And the cours have to
  add up to what TMDB says the season holds, or no address is offered.

  For a named arc the id-keyed providers are asked at the Kitsu address
  **instead of** TMDB's, not as well as. TMDB's is not sometimes-empty there,
  it is the wrong address - measured, it contributed nothing on either Bleach
  2x46 or 2x47 - and asking anyway is half again as many requests through a
  four-worker cap, every one of which the viewer waits through: **4217 ms
  against 1043 ms for the same three sources**. The two name-keyed providers
  still get the show's own name and absolute number, which is what they can
  answer. The plain shape - one TMDB season to one broadcast run - is asked
  only when the first question found nothing, because there TMDB's address
  usually works: KonoSuba S03E05 returns the same 39 sources either way.

  The arc address is not conditional on the first search failing, though. That
  looked cheaper and was wrong: Bleach 2x47 came back with one wrongly matched
  result from a name index, and that single result was enough to stop the
  address that actually had the episode being tried.
* **`_a_different_series` was rejecting most of the anime it exists to
  protect, on the exact titles it should have known.** Asked live for what
  "still not good for anime" meant, a sweep of 1,605 real Torrentio/TorrentsDB
  releases across 30 popular shows found **1,241 - 77% - scored as a
  different show sharing the name**, before any release was even looked at
  for resolution or seeders. Four separate word-matching gaps, stacked:

  * A macron folds to nothing. TMDB spells this show "Naruto Shippūden" and
    no release ever reproduces the accent - most write "Shippuden", plain
    ASCII - so the word the metadata carried and the word every real release
    carried were permanently different strings. `release.normalise` now
    folds through NFKD and drops combining marks, which Hebrew has none of
    and passes through untouched.
  * A doubled vowel is the same long vowel spelled the other way. Of 32 real
    Naruto Shippuden releases, 13 - HorribleSubs, Hatsuyuki, DBD-Raws, all
    major groups - write "Shippuuden", the older double-letter Hepburn
    convention the macron fold never reaches because there is no macron to
    fold. `oo` and `uu` now collapse to one letter in the same function, so
    a macron, a doubled letter and the plain spelling all land on one string
    - and because both sides of any real comparison fold the same way, a
    genuine match still matches.
  * The English title is not the only one a release is named after. TMDB
    only ever supplied the English name, and fansub and raw groups routinely
    use the Japanese romaji one instead - "Shingeki no Kyojin" for Attack on
    Titan, "Boku no Hero Academia" for My Hero Academia - which `known`
    never held, so every one of those was scored as a different show.
    `meta["aliases"]` existed, was read by `_a_different_series`, and was
    never once written by anything - dead code waiting for exactly this.
    `tmdb.romaji_titles` now fills it from TMDB's own `alternative_titles`,
    filtered to `iso_3166_1: "JP"` and `type` containing "romaji" - not the
    native-script entry beside it, and not the "initialism" ("SNK", "AOT"),
    too short to be worth the collision risk.
  * Punctuation glues rather than separates. "Boruto:" (TMDB's own title,
    colon attached) and "Boruto" (the release) were two different words, and
    "Re:ZERO -Starting Life in Another World-" collapsed into one long glued
    token nothing could ever match against a release spelling it "Re Zero".
    A local fold - colons, apostrophes, bare hyphens, question and
    exclamation marks all become spaces - runs only inside this function,
    never in `release.normalise` itself: several of that function's own
    resolution and codec patterns are tuned against exactly what it
    currently leaves in place, and this is the only caller that needs
    punctuation gone rather than kept.

  Fixing the words did not fix the split. The function reads everything
  *before* the episode number as the series name, and the marker for
  "where the episode number starts" had three of its own gaps: a
  four-digit padded number ("S01E0001", ordinary past a thousand episodes -
  One Piece is there) was one digit past what `s\d{1,2}e\d{1,3}` allowed, so
  the marker never matched at all and "One Piece - S01E0001 - I'm Luffy! The
  Man Who's Gonna Be King of the Pirates!" read its own episode title as a
  second show's name; a bare "EP01" or "E001" with no season number in front
  - ordinary on a single-season show - was not covered by either half of
  the old pattern; and SubsPlease's own two-part convention, "01A"/"01B",
  glues its letter on with no space and so never had a boundary to split at
  either. A widened marker (`s\d{1,2}e\d{1,4}[a-z]?`, plus bare `e\d{1,4}` and
  `(?:episode|ep)\s*\d{1,4}`, each with the same optional trailing letter)
  catches all three. And what a marker like that leaves *before* it needed a
  vocabulary of its own: "[HDTV 1080p][Cap.101]" put "hdtv", "1080p" and the
  Spanish release-scene word "cap" (capítulo) in front of the number the old
  pattern did find, and with nothing recognising a resolution or a codec as
  ordinary release furniture, both read as a second show's name.
  `_STRUCTURAL` now carries the common ones, the same vocabulary
  `release.py`'s own tables recognise, duplicated locally rather than
  imported because this function never parses a size or a bitrate, only
  asks "have I seen this word before".

  Measured after, same 1,605 releases: **670 flagged, down from 1,241 - 571
  recovered**. Demon Slayer went from 38 flagged to 0. Most titles dropped to
  single digits. What is left is not a new gap: Cowboy Bebop's remaining 25
  are Korean dubs and an unrelated isekai anime that Torrentio itself
  returned under the wrong IMDb id, which this function exists to catch and
  is catching correctly; Boruto is still rejected against a plain "Naruto"
  query, which is right, because it is a different show. One Piece is the
  outlier that stayed high - 467 of 500 - and the reason is legible in the
  examples: absolute episode numbers past a thousand, half-caught aliases in
  Chinese script no Latin word-matcher can read, and a long enough tail of
  naming conventions that the next gap here is a question of diminishing
  returns rather than one more clean fix.
* **Anime, series, films and VOD are four engines, not one heuristic.** The
  "different show sharing our name" check was one function serving anime
  and ordinary television alike, and every fix for one regressed the
  other. `scoring._a_different_series` is now only a dispatcher on
  `meta["extra"]["anime"]`:

  * `_a_different_anime_series` - romaji aliases, absolute numbers, the
    anime release vocabulary. Untouched by the split, so it could not move.
  * `_a_different_western_series` - knows the show by every name it is
    sold under. On a Hebrew interface `title` is Hebrew and a Korean
    drama's `original_title` is Korean, and every release of Squid Game is
    called "Squid Game": **98 of its 99 releases** were being rejected.
    `play._name_it_in_every_language` puts TMDB's translations
    (`translated_titles`, which carries "Juego de tronos", "Il Trono di
    Spade", "Hra o trůny") and `english_title` on the meta - series only,
    one cached call. It also has its own episode markers ("1x01",
    "S01.E01", a bare "S05" before "01") and its own season words
    ("Stagione", "Temporada", "Saison", "Staffel"), because a Spanish or
    Italian upload uses them and anime's list never needed to.
  * Films never reach either: `_a_different_film` is theirs.
  * VOD is its own path entirely and has **no subtitles**: a broadcaster
    stream is marked `broadcaster` and both `subs/auto.py` and
    `subs/service.py` stop there, and a programme played from the VOD
    screens never hands the player any metadata at all.
    `test_a_broadcaster_stream_searches_no_provider` pins it.

  Measured before and after on the same releases (HEAD's scoring against
  the new one, Hebrew-interface metadata), 4,659 releases over 32 titles:

    films    kept 1461 -> 1461    identical
    series   kept  767 ->  983    "another series" 292 -> 76
    anime    kept 1252 -> 1252    identical, by construction

  Money Heist went from 31 to 57, Friends 78 to 93, Game of Thrones 76 to
  97. It also knows a show by its initials and by "and" for "&": every
  release of Law & Order: Special Victims Unit is "Law.and.Order.SVU", and
  eight of thirteen copies of 5x18 had been refused as another show
  (`scoring._initials`, runs of three words and more). "Drake&Josh" is
  uploaded glued, so "&" also splits a word. And by its other names in its own language: Star Trek is "The Original
  Series" and "TOS" on every release, which TMDB's alternative titles carry
  and translations do not - 19 of 23 copies of 1x23 were refused. Two
  neighbouring words glued ("Startrek") count too (`scoring._joined`). What the series engine still rejects is mostly right - "The Game"
  for Game of Thrones, Sealab 2021 for Stranger Things, Little House on
  the Prairie ("La Casa De La Pradera") for Money Heist - and a test pins
  each of those. **The rule from here: a change to one engine is gated by
  that before/after run on all three, and the other two must come out
  identical.**

  **The anime engine's second round, and the gate earning its keep.**
  Asked why Naruto Shippuden 3x55 read 71% everywhere: the 71% subtitle was
  "[HorribleSubs] Naruto Shippuuden - **108**". TMDB numbers that show
  absolutely *inside* each season - season 3 is episodes 54 to 71 - and
  `absolute_episode` added the earlier seasons on top, so 55 became 108 and
  the wrong episode's subtitle was offered with a straight face. A season
  whose first episode is not 1 is already absolute; the season list is the
  same cached call `episodes()` makes. With it fixed the honest number is
  45%, because OpenSubtitles has no proper English file for that episode -
  a lower true number beats a higher wrong one.

  Also, anime only: the subtitle name query runs even when the id query
  answered (Naruto's id query returned "055_LEG" and "155_LEG", which
  stopped the search that finds fansub uploads); version suffixes ("02v3")
  and foreign episode words ("Jakso") end the show name; and `anime_titles`
  replaced the Japanese-romaji-only list. Digimon's release name, "Digimon
  Adventure", is tagged US and IT - TMDB's English name is the dub's - so
  26 of its 34 releases were hidden. Every romaji name is still kept, arc
  names included, and other Latin names are added unless TMDB labels them
  as another part ("series 2 title" is the sequel).

  The first version dropped every alternative title labelled with a season,
  and the gate caught it before it shipped: Demon Slayer 49 -> 46, because
  "Kimetsu no Yaiba: Hashira Geiko-hen" is an arc, and on TMDB an arc is a
  season of the same show. Measured after: films 551 -> 551, anime 476 ->
  518 with no title lower (Digimon 2 -> 15).

  **The anime subtitle scorer is its own, and a superset.** `matcher.rate`
  dispatches on `target["anime"]`: films and series go to `_rate_scene`,
  unchanged, and anime to `_rate_anime`, which runs `_rate_scene` first and
  then *adds* only what an anime name states and it cannot read - the fansub
  group at the front (OpenSubtitles files "[AnimeRG] ..." as "AnimeRG. ...",
  which the parser read as a group of "pseudo"), a name that is only the
  episode number ("055_LEG" was 45, and so was the wrong episode "155_LEG",
  which is now 0), a track extracted from the same MKV ("..._track3_[eng]"
  is identical to its release, so 100), and the Blu-ray or broadcast cut.
  A separate ladder with smaller weights was tried first and the subtitle
  gate caught it lowering every anime title whose subtitles are named
  scene-style; the superset cannot lower anything but a wrong episode.
  Measured with `tools/subtitle_gate.py`: films and series identical, anime
  AI-source average 88.8 -> 90.7, English 88.0 -> 88.4 (Spy x Family's AI
  list 77 -> 91, Frieren 1x20 86 -> 93). The anime name query is also
  asked only under the absolute numbering, which is how fansub uploads are
  filed, rather than once per numbering.

  **A subtitle shipped with the release now counts in its fit.**
  `sources.bundled` has long looked inside cached torrents in the
  background and remembered which languages ship beside the video - and
  that reached the screen only as a mark, while the same row's fit went on
  quoting a stranger's upload at 70%. `outlook._shipped` reads that memory
  (a cache read, so the draw never waits) and a release shipping Hebrew,
  English or a translation source is 100 on that list: in time by
  construction, and the file playback's sidecar provider asks for first.
  For anime an unlabelled file counts as English, which is what a subtitle
  in a fansub torrent is; it is remembered as `und` and never printed. This
  mostly helps films and series: checked live against 18 cached anime
  releases (Digimon, Frieren, Jujutsu Kaisen), **none** shipped a separate
  subtitle file - SubsPlease, Judas, Sokudo and the rest put it inside the
  MKV, which is the next thing to read.

  **And now it is read: the tracks inside the file.** `utils/matroska.py`
  reads the track list out of the first 512 KB - Matroska declares its
  tracks before the first cluster - and `sources.bundled` does it in the
  background for the top six cached rows, remembered per *file* (a season
  pack holds every episode) for a year. Checked live on twelve cached anime
  releases before building it: **every one had a full English track
  inside**, several with Arabic, Russian, French and Spanish too. Tried
  first through TorBox's own `stream/createstream`, which knows the tracks
  - and answers `PLAN_RESTRICTED_FEATURE` on this account.

  A track inside the file counts for the Hebrew and English lists, never
  for AI: Kodi shows a muxed track directly, which is what the automatic
  path picks first, while the translator needs a subtitle it can download.
  Signs-and-songs and forced tracks are not counted - they caption on-screen
  text, not dialogue. A track naming no language is English, by the
  Matroska specification rather than a guess. The pass uses at most two of
  the shared pool's four workers, so it can never make playback wait.

  Measured end to end on Frieren 1x20: the English list read 95, 91, 91,
  80, 80, and after the 18-second background pass **100 on every row**.

  **Where anime naming belongs next.** TMDB's labels are free text in a
  dozen languages ("Season 4", "season 4 title", "OAD title romaji", Korean
  prose), so tying a name to a season from them is guesswork. Kitsu and
  AniList model each cour as its own entry with its own English, romaji and
  synonym titles, and `kitsu.episode_address` already finds that entry for
  the season being played. Taking the anime engine's names from it, with
  TMDB as the fallback, is the principled version of `anime_titles`. And
  the ceiling on anime subtitle accuracy is not the corpus at all: fansub
  releases say "softsubs", "Multi" and "Dual Audio", and carry an English
  track inside the MKV that is in time by construction. Reading that track
  list for a cached source is what would put 100% on an anime row.
* **Three hundred anime episodes, searched the way the picker searches
  them, and what that found.** `tools/anime_survey.py` draws 75 episodes
  each from popular, old, new and obscure shows, random seasons, and runs
  `build_meta`, `aggregator.find` and `outlook.split_rows` for real, with
  the in-file track probe. Every miss was then looked up by hand - on the
  tracker, on OpenSubtitles under every id, name and numbering - so that a
  gap upstream is never counted as a defect here, or the other way round.

      round 3 (before)   playable 90%   subtitle route on playable 92%   AI 71%
      final              playable 91%   subtitle route on playable 97%   AI 83%

  Every episode with no playable source is a show nobody releases in a
  form this can use - adult shorts that exist only as Japanese raws,
  Doraemon and Chibi Maruko-chan as Chinese raws, Atashin'chi as one
  663-episode batch. Every playable episode with no subtitle row was
  checked, and has none anywhere in a language this reads. "5 or more
  cached" *fell*, 82% to 75%, and that is the survey telling the truth:
  Torrentio's "[TB download]" links were being counted as cached, which
  TorBox then refused to play (Mazinger Z: 5 "cached", 2 real).

  What it found, each fixed and measured on the episodes that exposed it:

  * **The numbering TMDB folds away.** Jujutsu Kaisen's "1x41" is S2 - 17 to
    every release and every subtitle; sources were found at the Kitsu
    address, subtitles were asked for at 1x41 and found nothing.
    `aggregator._scene_episode` reads the numbering the id-keyed releases
    agree on (a clear majority, anime only) into `meta["scene"]`, which the
    subtitle numberings and the matcher both accept. Re:Zero, Apothecary,
    Black Lagoon, Monogatari, Bleach and Duel Monsters went from an empty
    AI list to a full one.
  * **A season with a break in it is two seasons.** Hell's Paradise season
    one is 25 episodes, thirteen in 2023 and twelve in 2026 - under the
    length rule, so 1x22 was asked only at TMDB's address and found four
    files. `_folded` now also counts a break of 90 days before this episode,
    and such a season is asked at **both** addresses, because which one the
    trackers use varies: Hell's Paradise 1x22 has 36 at Kitsu's and none at
    TMDB's, Snow White with the Red Hair 1x21 ten at TMDB's and five at
    Kitsu's. Where no release states a numbering, the break supplies it:
    every Snow White release calls it "21" and OpenSubtitles files it
    S02E09, the ninth episode after the break. MASHLE 5 -> 24 sources.
  * **A named season is filed under its own name.** Ace of the Diamond's
    "Act II" is "Diamond no Ace - Act II - 34" on OpenSubtitles, episode 34
    of season one; the show's name under any numbering finds nothing.
    `opensubtitles_rest._by_season_name` asks "<name> <season name>", once
    in every language, first spelling that answers, cached. FLCL
    Progressive and Shoegaze, Fighting Spirit Rising, Full Metal Panic!
    Invisible Victory, Bleach TYBW.
  * **A name query answers by full text.** "mono" episode 7 came back as
    Neon Genesis Evangelion, "Yu-Gi-Oh! GX" as Yu Yu Hakusho, "blood" as
    Bleach: Thousand-Year Blood War, Death Note as a BBC drama called
    Campion - and the picker drew them at 70%, because they name the right
    episode number. A row whose filed show name is not mostly words this
    show goes by, under any of its names, is dropped. Measured on sixteen
    titles: every row it removed was another show's, and none of the real
    ones went.
  * **Punctuation the index redirects.** "+" and a trailing space answer
    302 to a host called "_", a ConnectionError every time - Blood+ hit
    the deadline on every episode. "/" is a path, "&" and "'" cost a 301.
  * **A name index answering fast is not everything answering.** Nyaa came
    back in a second with two files nothing would keep, Torrentio was still
    working at ten, and it was never asked again because something had come
    back. Black Lagoon 1x20 and Cowboy Bebop 1x22 went from 0 to 22 and 54.

  And then Naruto Shippuden 3x55, looked at row by row, which the survey's
  counts could not have shown:

  * **Cached is not playable.** Five of its ten "Cached TorBox" rows could
    not be played: four were complete-series batches that TorBox holds as
    one .zip ("[Batch] [pseudo].zip", 109 GB), one a zip of another episode
    named "s03e02.mp4". Torrentio lists each under the episode's own file
    name. `torbox.playable` asks for the file lists of the top twenty cached
    rows, five at a time, remembered for thirty days, and a row with no video
    file in it leaves the list - "held by the debrid service with nothing to
    play" in the picker's hidden count.
  * **A release can name less than our show.** "Naruto 055 [Nezumi]", out of
    a pack called "Naruto 053-078", is episode 55 of the original series:
    the extra-word check had nothing to object to, because it says *less*.
    `scoring._names_only_the_parent` rejects a release named by the first
    part of our name when the rest is part of the name ("Naruto" for Naruto
    Shippuden, "Dragon Ball" for Super) and not a subtitle after a colon or
    dash ("Frieren" for Frieren: Beyond Journey's End is ours). The engine
    gate's only "regression" for it, Naruto Shippuden 38 -> 35, is three
    releases of the original Naruto's first episode.
  * **What the background learns is drawn at once.** Every English row read
    75% while two of the files carry an English track inside - learnt in
    the background after the picker opened, and drawn only on the next open.
    `bundled.annotate` now calls back when it learns a track, and the picker
    rebuilds its page and keeps the cursor on the same release. Checked in
    a real Kodi, not only the stubs: "2 of 5 releases carry their own
    subtitles", then the second render, with the window still up.
  * **There is no Hebrew for Naruto Shippuden anywhere this can reach.**
    Wizdom has no entry for it, OpenSubtitles has Hebrew for the original
    Naruto's episodes and none for Shippuden's, and Ktuvit lists four
    seasons with no subtitle in any of them. Checked, not assumed.

  Two things about measuring it. OpenSubtitles throttles a burst - the
  survey sends dozens of requests an episode, which a viewer never does -
  and answered `ConnectionError` or 503 maintenance for stretches, so every
  empty subtitle list was re-asked before being believed. And the outlook
  cache holds an answer for an hour, so a fix measured within the hour
  reads the answer from before it.

* **Three hundred films and three hundred series episodes, the same way.**
  `tools/anime_survey.py --kind film|series`. Films: 293 of 300 playable,
  every playable one with a subtitle route, Hebrew 88%, AI 98%; the seven
  that are not are still in cinemas or not out. Series are two populations
  and are reported as two, because TMDB's "popular" television is full of
  daily soaps, talk, news and game shows nobody releases:

      regular   245 episodes   playable 97.6%   route on playable 92.9%
                               Hebrew 84.9%   AI 92.9%   English 90.8%
      daily      54 episodes   playable 0%    (Coronation Street, GZSZ, C.I.D.,
                               Hollywood Squares, The Situation Room, ...)

  The regular gaps are upstream: Rififi and a three-day-old episode have no
  release; Sins and Roses, Alıkara and Far Away (Turkish) and Against the
  Current (Chinese, Vietnamese subtitles only) have nothing to translate
  from; Girlfriends (2000) has two subtitles in all of OpenSubtitles.

  What it found was naming, all in the series engine: "Grey`s" and "IASIP"
  (an apostrophe split the word and the initials), "S11E17E18", "S0613",
  Portuguese "T01E10", "S04E03a", "TheWireS01E10", "Black list", a site
  prefix ("www.1TamilMV.meme - ", "(AnimesTotais)") and a scene group's
  ("ppt-sliders"). Widow's Bay went 7 -> 52 kept, Modern Family 11x17
  18 -> 29, Stargate SG-1 +4 an episode. And one in `release.normalise`
  for everything: a dotless "ı" has no decomposition, so every "Alikara"
  was another show than "Alıkara" - Turkish drama, which this household
  watches, went from nothing to playable. Films and anime came out
  identical on the gate; what the series engine still refuses is "The
  Flash" for Hawaii Five-0, "NYPD Blue" for Law & Order, Russian
  transliterations and typos ("Sucession", "Ted Laso").

* **A Hebrew subtitle at 99% can be another show's, and the surveys could
  not see it.** They count whether a row exists and how well its *name*
  fits. Asked why CSI: Miami and Gilmore Girls had no Hebrew row, the
  episodes turned out to be missing upstream (Wizdom has Hawaii Five-0 to
  season 6, Longmire to season 1) - and Wizdom's whole-show lists showed
  something worse: **it files an upload under every show whose title
  contains the name it was uploaded with.** Over 70 surveyed series and
  23,218 names, 17 shows carried another show's subtitles: HBO's Girls under
  Gilmore Girls (69 of 191), CSI under CSI: Miami (60 of 159), The Middle
  under Malcolm in the Middle (50), Lost Girl under Lost (74), Star Trek:
  TNG under Star Trek (26), Dexter: New Blood under Dexter. They carry the
  right season and episode, which is all the matcher asked: Gilmore Girls
  6x05's best Hebrew subtitle was "Girls.S06E05.720p.HDTV.x264-AVS" at 99,
  ahead of the real one at 85; CSI: Miami 9x13's only one was CSI's; Hacks
  1x02's was "Half.Man.S01E02", at 99.

  Words cannot settle it - "Buffy", "SG1", "Lois and Clark" and "Hawaii
  Five" are ours, "Girls" is not - so `subs/othershow.py` asks TMDB whether
  a *different* show goes by the name: one cached search per name that is
  not plainly ours, at most eight, in the shared pool under a four second
  deadline. A franchise's short name ("CSI") is its first show's. It runs in
  `auto.search_candidates`, which every route passes through; a hash match
  is exempt, and films and anime are untouched. Measured on what the
  providers answered for 239 episodes: 24 of 1,412 Hebrew candidates dropped
  and the top one was another show's on three; none of the 305 names it
  drops from the whole-show lists is ours.

  Four more of the same family:
  * **A film's sequel carries its title and another year.** Playing Dune
    (2021) from a FLUX release, "Dune.Part.Two.2024...FLUX" scored 99. The
    year alone is not the test - "Blade.Runner.The.Final.Cut.2007" is the
    film, dated by its re-release - so `matcher._a_sequel` wants a far year
    *and* a name that is not ours give or take an edition, or a year
    *before* ours, which no re-release has. Over 298 films and 2,520 names
    it rejects two, both right, and the plain year rule it replaced was
    wrong on three.
  * **One file, two episodes.** ALF's only Hebrew subtitle for 3x05 is
    "S03E04-05" and was the wrong episode; "S11E17E18" parsed as no episode
    at all. `release.parse` reads `episode_last`, `matches_episode` takes
    the span - which the debrid file picker uses too - and the matcher gives
    the episode's weight only when the release holds the same span, since a
    double-episode subtitle is an episode out on a single one.
  * **A show called "It" switched a check off.** `opensubtitles_rest.
    _names_the_show` compares words of three letters, "It" has none, and
    with nothing to compare it passed everything: The Diplomat, The Ark and
    Killing It for It 1x02, twelve rows and none of them ours.

* **Every survey here ran faster than the picker does, and so measured a
  better add-on than the one on the screen.** "I barely see any Hebrew" was
  true and none of the numbers above could show it. In a real Kodi, Naruto
  Shippuden 3x55 logged `deadline hit after 10.0s, dropped:
  opensubtitles_rest` twice, `subtitle outlook: 0 candidates`, and `action
  episode took 43679 ms`; the second opening drew "0 candidates" at once.
  Three causes, each invisible from a long-lived test process:

  * **Sixty-three requests for one episode, in a row.** The translation
    round asked OpenSubtitles per language - three numberings by id and
    three spellings of the name under two, nine a language, seven
    languages - on one worker. Leaving the language out answers with all of
    them (`opensubtitles_rest._all_at_once`), cut at 100 rows, so a full
    answer is distrusted and asked language by language as before. And
    "shippūden", "shippuden" and "shippûden" are one query to the index:
    identical eighty rows. Over fifteen titles: **184 requests to 51, no
    candidate lost, fifteen gained** (Latin American Spanish is filed as
    `spl`). Naruto's picker went from 24.6 s to about 10, Hikaru no Go's
    from 28 to 3.8.
  * **A search the deadline cut short was remembered for an hour.**
    `run_parallel` drops what has not finished and returns the rest, which
    is not an exception, so "a failure is not cached" never applied.
    `auto.cut_short` says when it happened and `outlook` does not store that
    answer.
  * **Ktuvit signed in on every search.** Its session was process memory,
    and Kodi runs every press as a new Python: homepage for the salt (five
    to eight seconds on its own), login, then the search - at the deadline
    or past it. The session is a window property now, which is Kodi's
    memory and not storage; the salt, which is public, is cached on disk;
    and Ktuvit is asked second rather than last. It is the only Hebrew
    source for 8 of 239 episodes and carries a release name nobody else has
    on 102, so losing it cost rows and, more often, the fit.

  And what "Stremio and POV IL have Hebrew for this" turned out to mean.
  Checked by hand on 1 October 2026: Naruto Shippuden has **no** Hebrew
  subtitle on Wizdom (0 names) or Ktuvit (four seasons listed, every episode
  "אין כתוביות"), under 3x55, 1x55 or S03E02; Hikaru no Go and Black Lagoon
  are not on Ktuvit at all. POV IL's subtitle add-on translates with Gemini
  Flash-Lite on the fly and shows the result as Hebrew; Stremio's "Heb Subs"
  lists Machine Translation as a source, and "כתוביות בעברית" adds SubDL and
  Subsource, which want a key. Stremio's own OpenSubtitles add-on has no
  Hebrew even for Fight Club. So for anime the gap is presentation and a
  working engine, not a source: the same translation is here, drawn as one
  yellow "LLM" row under "No Hebrew subtitle exists".

* **Against Kodi POV IL's MoranSubs, run rather than read.** Its engine
  (`service.subtitles.kodipovilai`, 71,000 lines) was run unmodified beside
  ours on 79 titles with the same release name, search only, each side
  judged by both rulers. A harness lesson first: our Kodi stub does not
  convert language codes, so its OpenSubtitles source asked for "heb,eng",
  found nothing, and for an hour looked broken when it was the test.

      class          n   has Hebrew   exact name   seconds
                         them / us    them / us    them / us
      film popular  20    80 / 80      50 / 55     8.7 / 1.2
      film new      15   100 / 100     93 / 93     6.7 / 1.2
      series        25    96 / 96      56 / 56     5.4 / 0.9
      anime         19     5 / 5        0 / 0     10.0 / 0.9

  **Level on finding Hebrew, title for title** - neither found one the other
  missed - and about seven times faster, because it waits out dead sources
  (Subscene, BSPlayer) to its ten second limit. English to translate from:
  anime 53% against our 68%. What it has that this does not, in order of
  what it costs to match:

  * **The file's own subtitle track, as a ruler and as a source** - built,
    below.
  * **OpenSubtitles.com**, through 33 API keys, and **Ktuvit** through one
    shared account. Coverage came out level anyway. DarkSubs' author has
    since allowed the keys to be used here - below - and the Ktuvit
    account has not been asked about.
  * **A community pool**: a server where a translation made by one user is
    served to the next. It is their private service behind a signing key,
    so it was not queried and cannot be matched without one of our own.
  * **Telegram channels**, off until the user signs in.

* **The subtitle track inside the file is the ruler, and the source.**
  `subs/inside.py`. mkvmerge indexes a subtitle track line by line: every
  cue has its start, its duration and where its block sits, in the Cues.
  Measured on cached releases: 98 KB for Fight Club's 1,810 lines, 706 KB
  for a Silo episode's 43 tracks, read in well under a second.

  *As a ruler* (`inside.timeline`, `auto.ruler_later`): those times are when
  somebody speaks in this cut, which is all `sync` correlates, so any
  downloaded subtitle can be measured against them - no hash, no second
  opinion. It leads `reference_cues` and `hash_reference`. On the cached
  copy of The Invite, four Hebrew subtitles fitting at 75% by name were all
  **19.4 seconds out**, and the file's Portuguese track said so exactly; on
  Dune: Part Two it refused a mislabelled one at 0.20. A file with no
  indexed track - the Friends Blu-ray rips - is as it was.

  *As a source* (`inside.text`, `auto.inside_source`): the lines are read
  from where the index points, a few kilobytes each, never the video
  between them, and lead every downloaded source in both translation paths.
  Naruto Shippuden 3x55's BDRip went from "No Hebrew found" to an AI row at
  100%: 334 lines of its own English, in time by construction.
  `bundled._remember_readable` learns which tracks can be read in the same
  background pass, so the picker promises it before it is pressed.

  The limit that shapes it is not ours. **The debrid CDN answers 429 to a
  client that asks too fast, and to the player with it**: 3,167 requests in
  38 seconds for one film's track, and every read failed for minutes after.
  So a track is read only when it is an episode's worth of lines
  (`MAX_LINES`), no closer together than `PACE`, and the first refusal ends
  it. Fourteen to thirty requests a second were measured passing. A feature
  film's track is still a ruler - that is one read - and its text is left
  to the subtitles that can be downloaded. An older fansub mux indexes only
  its video; such a track is neither.

  **A read that failed is not an answer, and was filed as one for a year.**
  Checked in a real Kodi on 6 October 2026: Naruto Shippuden 3x55's BDRip
  drew "EMBEDDED EN" beside "translated from AR, 70%", while the same file
  outside Kodi read 316 lines of its own French and English. `inside._read`
  answered `[]` both for "this file indexes only its video" and for "the
  request was refused", and the CDN had refused everything for a few minutes
  on 1 October; `bundled` remembered that `[]` as "nothing readable" for a
  year. A failed read is `None` now and is never stored, and the readings
  moved to a new key so every poisoned one is asked again. A file whose
  tracks were learnt before readability was asked at all is looked at once
  more for the same reason. After: the BDRip is the top row, 100%, from its
  own French.
  * `scoring._WESTERN_PREFIX` took ".Net"flix and ".To"rrents for the end
    of a site's name. Harmless where it was, wrong, and found by this.

* **OpenSubtitles.com is on, through the keys DarkSubs publishes**, with its
  author's permission. `subs/providers/opensubtitles.py` fetches the list
  from where DarkSubs fetches it, daily, keeps the last good copy a month,
  and puts a viewer's own key first; no key is in this repository. Measured
  on 6 October 2026 before relying on it: **search answers any key**, a junk
  one included; a download wants a real one (wrong key 503, spent 406), and
  the quota is **one counter for the caller, not one per key** - three
  downloads through three keys read 99, 97, 95 of 100. So walking the list
  buys a key that works, not more downloads.

  What it adds is not Hebrew. It is the same corpus as `rest.opensubtitles.
  org`: over 17 titles, the same Hebrew count on every one, and against the
  top five cached releases of each, **the same best fit on all 85 pairs**.
  What it adds is a second door to that corpus - the legacy one has refused
  downloads for stretches before (see "a download that returned nothing")
  - and English beyond the legacy provider's twelve a language. One request
  for every language unless the page is full: Fight Club in Hebrew and
  English is 129 rows, a page holds fifty, and six of those were Hebrew
  against thirty-three asked alone. An episode is asked under the show's id
  (`parent_imdb_id`), first numbering that answers.

  And it showed what the legacy door had been doing. **Every subtitle from
  `rest.opensubtitles.org` carried two adverts**: "Watch Online Movies and
  Series for FREE" for six seconds at the start and a minute of "Do you want
  subtitles for any video?" at the end - on a Hebrew file, in Hebrew ("נמאס
  לך לחפש כתוביות? Ray מייצר אותן מיידית"). The link says so,
  `.../filead/<id>.gz`; the same signed link with `/file/` is the upload as
  uploaded. An upload that was itself downloaded somewhere carries the
  advert baked in (Silo 1x01's PSA release, 638 cues against 636), so
  `srt.clean` also drops a cue naming those addresses.

* `meta/seadex.py` is the exception to ranking by numbers. For anime the
  release group *is* the quality, and SeaDex publishes which group won. It
  returns infohashes, the aggregator already merges by infohash, so a
  recommendation is a set of hashes to recognise rather than a name to match.
* **A title's own language is TMDB's `original_language`, set once on the
  meta by `play.build_meta`** - from the series, not the episode, because a
  TMDB episode carries no language and reading it from `meta["item"]` left
  every series blank. Four things hang off it:
  * A Hebrew title gets **no automatic subtitle** and the picker shows
    "Hebrew audio" without searching; the chooser still lists subtitles.
  * The Series tab has a **Turkish, Spanish and Italian dramas** row - one
    TMDB discover call, `with_original_language=tr|es|it`, drama genre.
  * **The source sort** gained two terms. A release only in a language nobody
    here reads now sorts *before resolution* (`_unwatchable`): as the -400
    weight it sat after size and lost only to a release of the same size, so a
    higher-resolution Italian dub beat an English release. And **a dub sorts
    below the original audio** right after resolution (`_dubbed`) when the
    show's language is not one the viewer reads - the subtitles follow the
    original dialogue. The dub stays in the list for a manual choice, kids
    mode is exempt, and MULTI counts as DUAL rather than DUB.
  * **The audio track** is switched to the original language on a file with
    several, or to Hebrew in kids mode; Kodi's audio menu still switches back.
* **Leaving the video pauses it, and Continue Watching needs no account.**
  Kodi's own answer to Escape is to return to the menu and keep the file
  running behind it, which is right for a media centre with a library and
  wrong for a debrid link on a gigabyte of RAM. `player.stop_if_left_behind`
  is asked by the service loop rather than by `tick`, because it is a question
  about the GUI and everything `tick` does is about the playing file. Paused
  rather than stopped, because stopping means the way back in is a whole
  source search again and the resume point only survives past one per cent.
  It fires once - `pause()` is a toggle, so a second one is the film playing
  again - and the counter resets only when the video is on screen. Two ticks
  rather than one, because the window is not always up on the tick after
  `onAVStarted` and killing a film somebody just started is the worse failure.
  A live channel and the radio are exempt: playing behind the menus is the one
  thing a television does that nothing else does.

  The `continue` row was declared `needs=("trakt",)` and `_continue_watching`
  only ever asked Trakt, so on a box with no account it could never appear -
  while the resume point was being kept the whole time in `bookmarks.json`,
  reachable only by finding the title again yourself. Trakt still wins where
  it answers, because it knows every box and this knows one.

  The row is drawn once, and the home window stays open under every video -
  so it kept what it held when Pinky opened. Top Gun: Maverick was saved at
  1:22 on 6 October 2026 and never appeared. Kodi calls `onInit` again when
  the window comes back from the video, and that now redraws the row
  (`home_window._refresh_continue`) when the titles or their order changed;
  a position saved every few seconds inside the same title redraws nothing.

* **Two presses racing was real.** A source search is seconds of network, so
  backing out and pressing something else left two in flight - and the
  cancelled title finished second, handed Kodi *its* URL, and the film that
  started was the one the viewer had left. Each press claims a ticket in a
  window property, which is the same value in every plugin invocation where
  module state is not, and it is checked before the picker opens and again
  before the hand-off. The ticket is random rather than the clock: `time.time()`
  on Windows moves in fifteen millisecond steps and two presses would claim
  the same one. A second AI translation is refused the same way and says so,
  because each holds a film's cues for minutes and both write the same file.

* `kids.py` replaces the home rows rather than filtering them. A TMDB list
  result carries no certification at all, so a filter alone would let
  everything through; the kid-safe rows ask TMDB for a ceiling instead.
  Because it replaces rather than filters, switching it has to **redraw the
  window** - `kodi.refresh_container` was being called and could never have
  worked, since it refreshes a directory and the dashboard is a window. The
  menu entry says what pressing it will *do* ("Turn kids mode off"), because
  a toggle that only names the topic is one you press to discover the state,
  and no PIN is asked either way: that guard existed so a child could not undo
  the mode from the menu they found it in, which is a real concern with the
  wrong owner on a television used by one family. `kids.check_pin` and its
  hashed store remain for whenever there is a reason to ask again.
* `subs/sync.py` is the part that makes a subtitle actually fit. It correlates
  speech activity as big-integer bitmasks and corrects constant offset,
  PAL/NTSC drift and **splits**. Its score is chance corrected, so an
  unrelated subtitle is refused.

  **The "about 170 ms" this used to claim was never true on a device.** It was
  measured on a Python with `int.bit_count()`, which arrived in 3.10, and
  Kodi 21 ships **3.8** - so every device took the fallback, and the fallback
  built a 72,000-character string for each of the 3,601 lags in a scan.
  Measured on a 2.8 hour pair, on the fallback: **0.78 s matching and 7.85 s
  unrelated**, on a desktop four to eight times faster than the projector. An
  unrelated pair is not the rare case - it is what refusing a wrong subtitle
  looks like, and it is the expensive one because only a good match trips the
  early break. `consensus` runs six to eight of them for one decision.

  Two fixes, both measured, now **0.16 s and 0.31 s** on that same fallback.
  The popcount translates bytes to their bit counts and sums, keeping all
  three steps in C - the obvious 256-entry table lookup is *slower* than the
  string it replaces, 516 us against 324 us, because `sum(tbl[b] for b in ...)`
  is a Python loop over nine thousand bytes. That was worth 24%. The rest was
  the *number of lags*: a one-second bin now answers "roughly where" over the
  full three minutes and the 100 ms pass only examines the window it names.
  That second pass engages only on feature-length timelines, because a
  one-second bin has to be sparse to discriminate and on a four-minute excerpt
  it is not - there the full scan is cheap anyway. `tools/bench.py` measures
  that path rather than the builtin no device has.

  **The timeline is clamped**, because this is where a stranger's numbers
  become the size of an allocation. `srt._TIME` accepts three-digit hours, so
  a downloaded file may name a cue ending 999 hours in; at a 100 ms bin that
  is a 36-million-bit integer. Measured before the clamp: **119 seconds** on a
  fast desktop for one such file, and `MemoryError` is the other outcome.
  `activity_mask` always took a `limit_bins` argument and not one of its three
  call sites passed it, so the default is the fix.

  The whole field agrees on the first half of this. ffsubsync discretises both
  sides into 10 ms "is anyone talking" bins and finds the shift that maximises
  overlap, using an FFT because the naive scan is quadratic; the big-integer
  masks here are the same idea reaching the same place, and finish in a tenth
  of the time because a subtitle reference needs no audio decoding. What
  ffsubsync's own documentation concedes is the second half: it has "difficulty
  with videos that have breaks or splits in the middle", and one offset is
  simply the wrong model for an advert break, a director's cut or a recap left
  in. alass exists for that, and solves it with a dynamic program over per-cue
  offsets where keeping the previous offset earns a `--split-penalty` bonus.

  `fit_segments` is that idea at a fraction of the cost: fixed cue-count
  blocks rather than optimal split points, and a block keeps its own offset
  only when a local search both clears an absolute floor and beats staying put
  by `SPLIT_MARGIN`. The floor is the part that matters - without it six blocks
  of an unrelated subtitle each find a different spurious offset and the wrong
  episode is assembled into place piece by piece. It is nearly free on a
  healthy file, because a block already sitting where the global fit put it
  scores well at zero lag and is never searched at all; only a block that looks
  wrong pays for a wide search.

  Measured on 22 September 2026, because no popular Kodi or Stremio add-on
  re-times at all and this had to earn its place: over 350 titles with real
  debrid streams, 12 had a hash-matched reference and 1 of them needed a
  split - a Supernatural special, whose fit rose from 0.64 on one offset to
  0.78 on two. One in twelve is a small sample, but it is a real gain on a
  real file and costs nothing on the other eleven, so it stays.
* **The broadcaster is asked before the trackers, not after them.** The two
  halves of this add-on held both answers and never joined them: רמזור sits in
  the VOD catalogue under Keshet while the source search reported nothing at
  all. "No sources" is the *ordinary* answer for Israeli television rather
  than a failure - the trackers are private, Sdarot was dissolved in 2023 -
  and the copy that exists is the one the broadcaster streams. So
  `play._broadcaster_stream` runs before the debrid check, because its stream
  needs no account, no torrent and no seeder.

  It has to come back with the **episode**, and that is the whole of it.
  Landing in a folder of four seasons after a press that meant "play episode
  three" is the add-on looking like it lost its place; that is what the first
  version did and it was wrong. Mako numbers nothing in a field - a season is
  "עונה 2" in the title and `-s2` in the address, an episode is
  "פרק 3 07.04.08 תמונות גולשים" - so the number is read from the word that
  names it and never from the first digits on the line, because every title
  carries a broadcast date and 07 is not episode seven. Two rows claiming the
  same number is a page this code has not understood, so it falls through to
  the trackers rather than guessing. Measured: רמזור 1x03 resolves to
  `.../Ramzor/S01/Ramzor_03_070408_VOD_plp249/...` in 0.73 s to walk and
  1.23 s to resolve.

  The name is the only join there is, because the catalogue carries no TMDB
  id, so the match is exact and never a substring, and only a title whose own
  language is Hebrew is looked up at all.
* `vod/` is Israeli television. Live channels and the on-demand catalogue are
  separate sections on purpose, because they are browsed differently. A
  programme **opens on its seasons** where the broadcaster has any, and the
  broadcaster decides what that means: Reshet numbers them, Kan puts the
  number in the address (`.../p-12463/s4/...`), Mako lists the seasons
  themselves as pages - which were being offered as things to *play*, so
  choosing one asked the player for a directory and nothing happened - and
  Now 14 files by month, which is grouped under that name rather than called
  a season it is not. Where a broadcaster numbers nothing the flat list is
  untouched, because one folder called "Season 0" is worse than the list it
  replaced, and one season is left flat because a folder holding the only
  thing inside it is worse than none. An entry belonging to no season - Kan's
  "watch the first episode" link - is shown after the folders rather than
  cancelling the grouping, which is what it did first and what left a
  four-season programme as one list of six hundred and twelve. Each
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
    python tools/setup_kodi.py --shortcut
                                      only (re)make the Pinky shortcut

Setup ends by putting a "Pinky" shortcut on the Desktop and in the Start
menu - the portable Kodi with `-p`, under `tools/pinky.ico` - built on the
machine it runs on, because a shortcut holds absolute paths.

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

2447 tests, all running against Kodi stubs, so no Kodi install is needed:

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
| `test_aggregator.py` | 36 | The orchestration the well-tested pieces hang off: top-K against "show all" (which used to return the same eight rows it was toggling away from), a debrid cache flag that must be able to come *down*, one batched question for a torrent three providers reported, and a provider that raises not taking the search with it. |
| `test_providers.py` | 21 | The Stremio adapter three of the four providers speak. Mostly about payloads that are not shaped the way the last one was: a size sent as a string or a float, a fileIdx that is not a number, and one unreadable stream costing only itself. |
| `test_anilist.py` | 10 | The anime catalog, and specifically that an outage upstream produces a hidden row and a log line rather than a broken screen. A failure is not cached as a result, so the row is retried rather than staying empty for the TTL. |
| `test_anime_numbering.py` | 81 | The three separate mistakes that made an anime episode unplayable, each found by surveying a thousand titles rather than by imagining it: the Japanese title searched against an index of romaji names, the season-relative number searched where an absolute one was needed, and nyaa not checking what came back. Plus the traps in reading a number off a name - a year sitting exactly where an episode number sits, a CRC, a version suffix, a batch range, and a season marker that makes the number season-relative. And two found by testing a Japanese source: `S01E66` being the 66th episode (Hikaru no Go's 3x06), and a stated season having to agree - "Oshi no Ko S3 - 06" is not 1x06. Plus `tmdb.romaji_titles` reading only the JP-tagged "romaji" entries out of TMDB's `alternative_titles`, and `meta["aliases"]` actually carrying one out of `build_meta` into a real rejection check. |
| `test_release_parser.py` | 101 | Resolution, source, codec, HDR, release group, season and episode, absolute anime numbering, Hebrew hints. Source ranking and subtitle matching both depend on it. Plus `normalise` folding a macron and a doubled long vowel to the same plain letter, live-measured against 1,605 real anime releases, and leaving Hebrew - which has no such decomposition - untouched. |
| `test_sources.py` | 162 | Merging the same torrent from several providers, and the filter and ranking rules: resolution ceiling, disabled codecs, HDR, cam releases, implausible sizes, cached-only, and a cached source always beating an uncached one. Plus every live-measured gap in `_a_different_series`: a macron, a romaji alias, punctuation gluing two words into one, a four-digit padded episode number, a bare "EP01" with no season, and SubsPlease's glued "01A"/"01B" split-episode suffix - each pinned to the real release that exposed it. And the series engine on its own: a Korean drama on a Hebrew interface keeping its releases, a title in another language being the same show, every episode-numbering form, and "The Game", Sealab and Little House on the Prairie still being caught. |
| `test_seadex.py` | 15 | The anime exception: a curated pick beating a far more seeded release, matching by infohash so a lookalike can never be promoted, a cached source still winning, an uncovered title costing nothing, and a broken SeaDex not breaking the picker. |
| `test_debrid.py` | 13 | Picking the right file from a season pack, ignoring samples and extras, refusing to play the wrong episode, and a repeated cache question not becoming a repeated API call. |
| `test_torbox.py` | 34 | The one debrid service with a live account behind it, tested against the shapes it really returns rather than the documented ones - `checkcached` answering with a list of objects and omitting a miss, `requestdl` answering with a bare string. Also which call goes first: the account list is 466 KB and two to four seconds, `createtorrent` answers "Found Cached Torrent" in half a second, and a torrent TorBox is still downloading is not played at all. |
| `test_opensubtitles_rest.py` | 64 | The only anonymous OpenSubtitles, pinned against what it really answers. The lowercased query, because one capital letter is a 302 the add-on cannot follow. The two anime numberings. And the one worth carrying elsewhere: an id query that finds nothing falling back to the title, and that answer not being discarded for naming a different IMDb entry of the same show - which is how Hikaru no Go had English subtitles nobody could reach. Plus the romaji alias asked alongside the English name and not instead of it, capped at two, and `search_title` winning over a `title` that can be Hebrew. |
| `test_opensubtitles_keyed.py` | 73 | OpenSubtitles.com through a key: one complete subtitle per candidate, never a CD1 half, and a malformed answer costing only itself. Plus the published key list - fetched only when there is no key of the viewer's own, kept when a refresh fails, a refused key followed by the next and the walk ending where another key cannot help - an episode asked under the show's id, a full page asked again a language at a time, and `pt-BR` being Portuguese. |
| `test_yify.py` | 11 | The one anonymous film subtitle source with no account and no key: a real captured row parsed into a candidate, the site's own inconsistent casing on the language name, films-only enforced without a request (a series IMDb id answers 404 live), and the slug-to-download-URL transform that needs no second page fetch. |
| `test_subtitle_matching.py` | 67 | Candidate scoring: hash match, identical release name, group, source, resolution, and the wrong episode pushed to the bottom. Plus the OpenSubtitles hash arithmetic. And the anime scorer as a superset: the fansub group with or without brackets, a track extracted from the same MKV, a bare episode number and a wrong one, the Blu-ray cut, and films and series never reaching it. |
| `test_othershow.py` | 17 | A subtitle filed under our show that names another one: HBO's Girls under Gilmore Girls, CSI under CSI: Miami, The Middle under Malcolm in the Middle. And what must survive it - "Buffy", "Lois and Clark", a transliterated title - plus the franchise rule, a hash match being exempt, films and anime untouched, a bounded number of questions, and a search that fails dropping nothing. |
| `test_subtitle_sync.py` | 10 | The alignment engine: constant offset, PAL/NTSC drift, refusing to shift an unrelated subtitle, and a feature-length alignment staying inside its time budget. |
| `test_subtitle_chooser.py` | 24 | The hierarchy the viewer sees: embedded first, then exact, then estimates, with the label each earns. Forced tracks marked and skipped. |
| `test_subtitle_ai_ondemand.py` | 37 | Asking for a translation on purpose, and getting one where nothing exists. The row appearing over a perfectly good Hebrew match, because that judgement is the viewer's; the search widening past the two configured languages, because a film with no Hebrew and no English usually has a Spanish one; a translation never overwriting the subtitle it was made alongside; and the hand-off to the background service, which is where the work has to happen. |
| `test_subtitle_pipeline.py` | 54 | The whole decision end to end: only one file ever downloaded, a hash-matched reference re-timing a mismatched subtitle, translation falling back correctly, and partial translations reaching the player while the rest runs. Plus the three ways a title ended with nothing while good subtitles sat behind the failure: a sick provider spending a budget meant for files it never delivered, a subtitle for another episode being applied because its score was zero, and a CD1 half ending the search instead of being passed over. Plus the source of a translation being put in time before it is translated, and never being its own timing reference. |
| `test_inside.py` | 13 | The subtitle track inside the file, from byte-built Matroska: every line timed out of the index, the fullest dialogue track chosen over a signs track and a hearing-impaired one, a file that indexes only its video being no ruler, the file's own timeline leading a hash match, the lines read from where the index points, an ASS line reduced to its words, a film's worth of lines not read line by line, and a refusal ending the read rather than being argued with. |
| `test_matroska.py` | 10 | The subtitle tracks an MKV declares, read from its first bytes and built byte for byte from the element layout: a full track against a Signs & Songs one, a track naming no language being English by the specification, Hebrew and the newer language tag, and "unknown" never being mistaken for "none" - a file that is not Matroska, a header cut off before its tracks end, a cluster first. |
| `test_aes.py` | 8 | AES, because one login depends on it and a cipher that is subtly wrong looks exactly like one that is right. Checked against FIPS-197 and NIST SP 800-38A rather than against itself, so none of the expected values came from this code. |
| `test_subtitle_orchestrator.py` | 35 | What happens when the things the subtitle search depends on misbehave. Every other subtitle test replaces the search and the download with fakes that only ever return data, so the code between a provider and the decision had never been asked what it does when a provider raises, answers nonsense, hangs, or hands back a dict full of junk. These inject at the real provider boundary and keep everything above it. Also the two stuck screens, the cancelled job that still wrote a file, and Kodi itself answering with an error envelope. |
| `test_ktuvit.py` | 32 | The only subtitle provider with an account: the password hashed on the wire, one login per day rather than per search, a stale session re-established exactly once, and the whole provider staying silent without credentials. |
| `test_translation.py` | 44 | The translator surviving a model that misbehaves: code fences, prose around the JSON, blank entries, chunks that fail and must be split. Plus no model name containing a version number, which has been retired under us twice, and a 404 striking a model off for the session where a 503 does not. |
| `test_translation_context.py` | 17 | Cast and gender reaching the prompt, and a gender-marking source language winning a close call without overriding a clearly better match. |
| `test_vod_seasons.py` | 17 | A programme opening on its seasons, and the three rules that decide when it should not: one season stays flat, a broadcaster that numbers nothing is left alone, and an entry belonging to no season is shown after the folders rather than cancelling them. Plus the two discriminators - the season Kan hides in its addresses, and the difference between a Mako season page and a Mako video. |
| `test_vod.py` | 30 | Israeli live TV and the catalogue: broadcaster ordering, referers carried through, relative paths given their CDN host, broken channels hidden, Hebrew substring search, and updating the bundled data invalidating the cache. |
| `test_entitlement.py` | 24 | The broadcaster ticket: one Akamai ticket covering every Keshet channel rather than one each, the on-demand CDN getting an AWS ticket instead because the two are not interchangeable, browsing never asking for one, a refusal leaving the URL unsigned rather than empty, and a failure never being cached. |
| `test_kan_mako.py` | 15 | An episode being a descendant of its programme, which is what stops the site's own navigation menu being listed as episodes, and Mako's on-demand streams being signed. |
| `test_reshet.py` | 19 | Reshet's numbering, which the broadcaster publishes wrongly. Season and episode come from the Hebrew title because the metadata fields disagree with it, and the API returns episodes unsorted. |
| `test_sport5.py` | 22 | Six megabytes of broadcaster JSON reduced before it is cached, a season's clips gathered into its programme, the manifest lifted out of the player URL, and the byte order mark that made the whole document unparseable. |
| `test_extractors_israeli.py` | 23 | Now 14, Sport 1 and 891FM, plus the check that every broadcaster in the catalogue has an extractor behind it. |
| `test_mdblist.py` | 19 | The list resolution staying bounded, the curator's order surviving lookups that finish out of order, a title TMDB does not know being dropped rather than blanked, and the API key staying out of the cache keys. |
| `test_kids.py` | 52 | Kids mode replacing the rows rather than filtering them, a pinned row order not being inherited, a warm cache not defeating it, the PIN being stored hashed and actually required to leave, and `catalog.peek` still saying None for a row that was never warmed. Also Continue Watching built from this device's own resume points when there is no Trakt account, newest first, with a title TMDB no longer knows dropped rather than drawn blank. |
| `test_windows.py` | 56 | The home and search windows: rows filled lazily, the hero following focus, the on-screen keyboard opening on the script the interface is written in, suggestions never overwriting what was typed, entering the add-on landing in the Pinky window, preloading past rows that come back empty, and typing surviving a Kodi whose Action has no getUnicode. Plus rows that grow as they are scrolled: one page for a row nobody touches, a ceiling for one they do, a page fetched off the GUI thread but never *added* off it, the cursor put back unconditionally rather than only when it looks like it moved, and a resting mouse pointer not paging through the catalogue on its own. |
| `test_details_window.py` | 24 | Information, seasons, episodes, back stepping out of the episode list before closing, and playing a show picking the next unwatched episode - walking on to the next season when one is finished, and never landing on the specials. Plus the one button acting on the episode its label names, and falling back to the next unwatched one on the season list where the label names none. |
| `test_sources_window.py` | 54 | The picker, which was crashing on every cached source before it had any tests at all. Plus what a release carries: the file list and the tracks inside the file learned in the background and never on the draw, remembered per file rather than per torrent, an unreadable file not re-asked on every open, and the background pass never taking more than half the shared pool. |
| `test_play.py` | 78 | From "the user pressed OK" to "Kodi has a URL": the picker always opening, the newest press cancelling the one still resolving, leaving the video pausing it and a live channel being exempt, an Israeli title reaching the broadcaster's own episode, the service a cached source goes to, whether a download may be started, and - the one that took an evening to find - a resolved link that will not open being treated like any other source that will not play, with the dead CDN node remembered so the next source on it is free. Plus a series carrying its English and translated names, and anime and films not paying for them. |
| `test_qr.py` | 96 | The QR encoder, against the specification rather than against itself, because a QR code that is wrong looks exactly like a QR code and the only symptom is a phone that will not scan it. The block table has to add up to each version's codeword count, all thirty-two format strings have to match the published list, the Reed-Solomon coder has to reproduce the worked example in the standard, and every symbol is taken apart the way a scanner would - undoing the mask, the zigzag and the interleaving - and has to come back as what went in. |
| `test_signin.py` | 32 | The one sign-in screen: which methods a service offers and in what order, a service with one way in not being asked, and the three answers a poll can give - done, not yet, and never going to work, which is the one that stops a screen waiting out ten minutes. Also that mistyping a replacement key does not sign you out of a working account. |
| `test_profiles.py` | 26 | Every low-memory setting actually lowering load, all profiles setting the same keys so switching leaves nothing stale, **the shipped defaults being the lean profile key for key**, and the visual-polish switch raising artwork without ever lowering a richer profile. |
| `test_wizard.py` | 7 | The one setup step that is not an account: light against richer artwork, with what each costs, and a device that is told it has room rather than quietly switched. |
| `test_urlsession.py` | 13 | The standard-library HTTP session that replaces requests: parameters, form and JSON bodies, gzip, charsets, and an HTTP error being a response rather than an exception. |
| `test_upnext.py` | 8 | The next episode, including across a season boundary, and the signal being well formed. |
| `test_backup.py` | 11 | Settings and accounts to a file and back: every changed setting and every key returning, buttons and defaults never written, a backup from an older version still restoring, anything else refused, the newer resume point winning, and import asking before it changes anything. |
| `test_upgrade.py` | 48 | That an update costs the viewer nothing. Twenty-two credentials, and re-entering them on a projector with a remote is the difference between an update people accept and one they refuse. Reads the source for anything writing inside the add-on folder, which an update wipes; installs a real release over a real one and checks the keys, the subtitles and the profile survived; and refuses a truncated download, a file that is not a zip, and one with no `addon.xml`. |
| `test_failure_paths.py` | 31 | Somebody else's free service misbehaving. 429 retried and 404 not, `Retry-After` honoured rather than the backoff and capped so an hour-long wait cannot freeze a search, truncated JSON, DNS failure - and **the deadline**, which is the rule the whole add-on rests on. |
| `test_hebrew.py` | 70 | Hebrew is half the catalogue, not an edge case: cp1255 and iso-8859-8 subtitles, a byte order mark landing in the first cue, substring search over 2,810 Hebrew titles, a Hebrew title beside a Latin release group, and a filename that has to survive Android storage. |
| `test_packaging.py` | 5 | The built zip staying under 600 KB, containing no build junk, rooted at the add-on id, and carrying every file the add-on needs. |

Three of these catch whole classes of mistake rather than one bug:
`test_imports.py` finds anything that will not load, `test_addon_integrity.py`
finds settings and routes that promise something with no code behind them, and
`test_packaging.py` stops the add-on quietly growing.

## Releasing, and updating a device

One zip runs on Windows, the U4 and the Mi Box. `<platform>all</platform>`,
pure Python, no `.so` and no `.dll`. Two things are imported: `xbmc.python`,
and `inputstream.adaptive` for the DASH live channels. `requests` stays
optional because `urlsession.py` covers it with the standard library. So there
is nothing to build per platform, and the whole problem is distribution.

### Branches, and the one thing that publishes

`development` is where the work happens; `main` only moves on a release. But
the branch is not what publishes - **a tag is**, and nothing else is.

    git push origin development       nothing happens
    git push origin main              nothing happens
    git push --follow-tags            .github/workflows/release.yml runs

`release.yml` fires on `v*`: it runs the suite, checks the tag against every
place the version is written, builds `repo/`, cuts the GitHub release with
both zips **and `addons.xml` plus its `.md5`** attached, **deploys `repo/` to
GitHub Pages** (`actions/upload-pages-artifact`, then `actions/deploy-pages`),
and then polls `noflevi.github.io/kodi/addons.xml` until it serves the tagged
version. That last step is the only automated proof a release reached the
devices; everything before it can pass while the site still serves the
previous version. `concurrency: pages` keeps two releases from racing for the
one site, and is not cancel-in-progress because the loser would be a
half-published repository.

The index is attached to the release because the in-add-on updater reads
`github.com/NofLevi/kodi/releases/latest/download/addons.xml` - a stable alias
for whichever release is newest. Without it that URL is a 404 and no device is
ever told there is anything new.

**Publishing needs no secrets.** `deploy-pages` authenticates over OIDC with
the workflow's own token (`pages: write`, `id-token: write`) and `gh release
create` uses `github.token`, so a release can be cut from any machine that can
push a tag and nothing has to be copied between them. There is no manual
publish script: if the workflow fails after the GitHub release was created,
re-run it from the Actions tab (`workflow_dispatch`).

**Nothing runs on a push.** `e2e.yml` and `upgrade.yml` fire on a daily cron
and on the button, and that is all: they are the slow ones - a dozen live
services, and a real release downloaded from the live site - and running them
after every commit is a run for every small change nobody reads. What they
watch for is *drift*, which happens on somebody else's schedule rather than on
ours, so a daily check is the right shape. A release is still gated, because
`release.yml` runs the whole suite itself before it publishes.

    python tools/release.py            patch bump, news, build, tag command
    python tools/release.py --minor    0.1.4 -> 0.2.0
    python tools/release.py --dry-run  say what would change, write nothing

The tag is load-bearing for the changelog too. `release.py` asks
`git describe --tags` for the span of commits to write into `<news>`, and
until tags existed that always answered nothing - so the news was silently the
last eight commits rather than what had shipped since the last release.
Annotated, always: `git push --follow-tags` ignores a lightweight one, which
looks exactly like forgetting to tag.

**Kodi only offers an update when the published version is higher than the
installed one.** The version sat at `0.1.0` through every change recorded in
this file, with `<news>0.1.0 - Initial skeleton.</news>`, so no device could
ever have been told there was anything new. `release.py` bumps both add-ons and
writes the news from the commit subjects, because a changelog nobody writes is
a changelog nobody reads.

### Where the files live

Kodi fetches a repository anonymously, so wherever it lives has to be
publicly readable. `NofLevi/kodi` is public, and releases are published to
**GitHub Pages at `noflevi.github.io/kodi`**, with `repo/` uploaded as the site
root so the URLs baked into `repository.pinky` need no path juggling.

**Why GitHub and not Cloudflare, which is where this used to be.** Cloudflare
Pages never answered a ranged read: every `Range` request came back 200 with
the whole file. Kodi does not read a zip front to back - it reads the central
directory at the end and then seeks to each member - so the 123-member add-on
zip meant a 475 KB download *per seek* on a television, while the 2-member
repository zip was small enough not to notice. That is precisely the split
that was reported: the repository installed, and installing the add-on took
Kodi down with it on Android. Measured on The Crew and Fishenzon, GitHub answers
206 with `Accept-Ranges` on a 2.9 MB zip, and it is where the rest of the Kodi
world already hosts. The Cloudflare project, its secrets, `wrangler` and
`tools/deploy.py` are all gone. Anything in this repository's history about
disconnecting a Cloudflare project to empty its deployment list is about a host
that no longer exists.

Nothing watches the repository, so a push cannot publish: a Pages deployment
exists only because `release.yml` ran on a tag.

**`repo/` is not in the repository.** It is built by `release.yml` - or by
`tools/build.py` by hand - at publish time. It used to be committed, and that
was a trap with a sharp edge: `release.py` tells you to `git commit -am`, and
**`-a` stages only tracked files**, while every release produces a zip under a
filename that has never existed before. So the index could name a zip that was
never pushed. Nothing in CI could catch it either, because `e2e.yml` runs
`build.py` and overwrites `repo/` in the checkout before anything looks at it.
Building at publish time removes the class rather than guarding it.

**The hostname is baked into every installed copy of `repository.pinky`** -
three URLs in `repository.pinky/addon.xml`, and the updater's in `updater.py`.
Moving host means republishing the repository add-on at the *old* address
first, version bumped and pointing at the new one, or every existing device is
stranded.

A custom domain in front of Pages is the real cure for that lock-in, because
then the name devices hold is one we own and the host behind it can be
replaced invisibly. It is worth buying the day boxes other than our own have
Pinky installed.

### On a device

Install `repository.pinky` once from a zip, then Pinky from within it; after
that Kodi updates itself. `tools/deploy_android.py` pushes the zips over adb,
which saves driving a file manager with a remote.

**inputstream.adaptive arrives on its own.** It is Kodi's add-on and a binary
one - the copy on this Windows box is 21.5.24, `windows-x86_64` - so it can
never be inside our zip, and for a long time the answer was a paragraph asking
people to go and install it. It was declared `optional="true"`, which is
precisely the instruction *not* to fetch it, so every box arrived with a live
TV section that silently played nothing. As a plain import Kodi resolves it
from its own repository before installing Pinky, on whatever platform it is.

The cost, stated because it is real: an unresolvable dependency makes Kodi
refuse to install Pinky at all, where before it installed and only the live
channels were dead. `kodi.has_adaptive` and the guard in `listing.py` stay for
that reason - a box that got past the check without it should still say so
rather than play nothing.

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

**Accounts must come through everything** - updates, renamed settings,
profile switches, a token refresh that goes wrong. Two ways that was not yet
true, found by auditing for it on 6 October 2026: the upgrade test's net
missed `realdebrid.refresh` and both `expires` settings, and a refresh answer
with no token in it would have been stored - an empty Real-Debrid token, or an
empty Trakt refresh token that signs the viewer out at the next refresh three
months later. Both refreshes now keep what they had, and
`ACCOUNT_SETTINGS_EVER_SHIPPED` in `test_upgrade.py` freezes every account
setting name: one that disappears from settings.xml is a value Kodi drops on
the next save, so removing or renaming one fails the build.

**A backup for what an update is not** (`backup.py`, Settings -> Tools):
an uninstall with its data, a reset box, or a second box to set up. Every
setting changed from its default - accounts and keys included - and the
resume points, to a JSON file in a folder the viewer picks, and back after a
yes. A name the backup carries that this version no longer has is skipped,
and the newer of two resume points wins. It says when it is written that the
file holds every key, because it does.

The one risky step is replacing the folder, so it is the one with care taken:
unpack to a temporary directory beside the add-on, refuse anything without a
parseable `addon.xml`, then swap and keep a backup until the swap succeeds. A
projector on wifi produces half-downloads, and installing one would leave an
add-on that cannot start.

## End to end, and what CI actually checks

    python tools/e2e.py             twenty checks, live and offline
    python tools/e2e.py --offline   only the ones that need no network
    python tools/e2e.py --json out.json

`tools/e2e.py` is the other half of the test suite. The stubs prove the logic;
this proves that the services the add-on depends on are still there, still
shaped the way they were, and still answer the questions we ask. **Every defect
in this project's history that the unit suite could not have caught was of that
kind** - AniList going dark, SubSource moving behind a login, TorBox growing a
device flow it did not have, an anime episode addressed at a season number
nobody indexes.

One check is not like the others, and it has a pipeline to itself.
**`tests/data/baseline-plugin.video.pinky.zip` is the real 0.0.1 tree**, taken
with `git archive` from the commit that released it, and it is **never
regenerated**. `an old install upgrades to what is published` unpacks it as an
installed add-on, puts keys and a subtitle in the profile beside it, and then
runs the flow a viewer runs - `check`, `download`, `apply` - against whatever
is live. Freezing the baseline is the point: the distance between it and the
current release grows with every change, so the question gets harder every
month instead of staying the easy one. Regenerating it would quietly turn the
only real upgrade test into the current release over itself.

It runs `check()` rather than fetching the zip itself, because `check()` is
where the version comparison and the *derived* zip URL live, and those are
what stop working silently.

`.github/workflows/upgrade.yml` runs it **on Windows and on Linux**, which is
not decoration. Replacing an add-on is renames and open file handles, and that
is exactly where the platforms differ: Windows will not rename a directory
something still holds, and compares paths without case. The bug that broke the
first real update anybody attempted - `addon_path()` coming back with a
trailing separator, so the backup was written *inside* the folder being
replaced - was a Windows error 87, and a Linux-only run would never have
produced it. `python tools/e2e.py --only upgrade` is the same thing by hand.

### Two channels: stable, and the branch

`update.channel` is `stable` or `test`, at expert level in the settings.
Stable is the last release, at `noflevi.github.io/kodi`. Test is whatever
`testbuild.yml` last built, pushed to the **`test-channel` branch** and read
from **`raw.githubusercontent.com/NofLevi/kodi/test-channel`**. It is a branch
rather than a folder on the Pages site because a Pages deployment **replaces
the whole site**, so two channels sharing one would delete each other every
time either published. Until a test build has been published that address
answers 404 - measured on 17 September 2026, with no test build yet.

A test build is versioned as a pre-release of what is current -
`0.0.4~dev.12` - which is what Kodi's own comparison expects and sorts
*below* the release it previews. Two consequences the comparison had to
learn, because both are invisible under a greater-than:

* `parse_version` reads three numbers, so `0.0.5~dev.11` and `0.0.5~dev.12`
  compare **equal** and the second would never be offered.
* Going back to stable means installing something numerically **older**, and
  refusing that would strand anyone who ever tried a test build on it.

So on the test channel *any difference* is an update, because a test build is
"what the branch is now" rather than "a newer version". The stable channel
keeps the greater-than, and a test asserts it does.

`.github/workflows/testbuild.yml` publishes one, **by hand only**, from the
Actions tab. Not on every push: a test build is a deployment, and a deployment
nobody asked for is noise.

### The tag is the only record of what was published

A Pages deployment is a folder of files a workflow uploaded; that is the whole
of what it is. So the tag is not bookkeeping, it is the link between a version
people are running and the source that produced it - and without it, "what is
in 0.0.1?" has no answer a year from now.

Which is why the tag is also the *trigger*: a release cannot happen without
one, so the record cannot be forgotten. The GitHub release beside it keeps the
zips and the index themselves.

### Releases are deliberate

A release reaches televisions and cannot be taken back from a box that
already took it, so it happens when somebody says so and not when a branch
moves. `main` moving is not a release; a tag is. Nothing here bumps a version
on its own.

The corollary is that `main` and the published version can differ, and that
is fine - `main` is what will be released next, the tag is what was.

### One release is one number

The version appears in six places: both `addon.xml` files, the `<news>` field,
the README's download links, the repository index and the line at the bottom
of the home screen. They used to be kept in step by hand, and the repository
add-on was bumped **independently** - which agreed only because both started
at 0.0.1 and every release since had been a patch. One `--minor` and the
add-on reads 0.1.0 against the repository's 0.0.4, and after that they can
never agree again.

`release.py` now writes one number into both. `test_addon_integrity` fails if
any of the six disagree, and `release.yml` refuses to publish a tag that does
not match all of them. The label on the home screen is read from
`kodi.addon_version()` rather than written down, so it is the version Kodi
actually installed and not the one somebody remembered to update - which is
the only version most people will ever see.

**It never stops at the first failure.** A run that dies on check three says
nothing about checks four to fifteen, and the whole point is to come back with
the list. Checks are marked required or not: a required failure is a broken
add-on, an optional one is somebody else's service having a bad day, and only
the first fails the run - a red build nobody can fix is a red build everybody
learns to ignore.

Two things it has to do that are easy to get wrong, and both were got wrong
first. `kodi.addon_path()` has to be pointed at the add-on, or the bundled
channel and VOD data are simply absent and every check against them passes for
the wrong reason. And `cached_only` has to be turned off, because it ships on:
with no debrid account, which is what CI has, every source is filtered away and
a perfectly healthy search reports "no sources for Fight Club".

### Why the workflow is a matrix

This add-on runs on three very different machines - a Windows PC, the U4
projector on Android 9, a Mi Box - and the Python is identical on all three.
`<platform>all</platform>`, no compiled anything. So what differs is never the
logic; it is the ground underneath it, and a run on one operating system says
nothing about the other:

* **Windows forbids characters Android allows**, and compares filenames
  **without case**. Android is case sensitive and allows nearly anything. A
  subtitle written under one name and read back under another works on one and
  not the other, and the symptom is a subtitle that silently never appears.
  `_filename_key` lowercases for exactly this reason, not for tidiness.
* **A Windows console is cp1252** and half this catalogue is Hebrew. The tools
  in `tools/` raised `UnicodeEncodeError` printing a title twice during this
  work, which is a property of the console rather than of the add-on - and was
  mistaken for a bug both times. The Windows leg sets `PYTHONUTF8`.
* **A zip built on one unpacks on the other.** A backslash in a member name is
  a folder on Windows and part of the filename on Android; two members
  differing only in case silently overwrite each other on Windows and do not
  on Android.

So the `platforms` job is a matrix over `windows-latest` and `ubuntu-22.04`,
running the suite, the packaging check and every offline check on both, with
`fail-fast: false` - knowing something is broken on Windows *and fine on
Linux* is most of the diagnosis. Linux stands in for Android: same Python,
same case-sensitive filesystem, same separators. The `live` job runs once,
because the services do not vary by operating system and asking somebody
else's API the same question twice is rude.

Both legs use **Python 3.8**, which is what Kodi 21 ships, so a walrus or a
newer f-string cannot reach a device. The runners are pinned rather than
`latest` because 3.8 is past end of life and the 24.04 image has no build of
it - `setup-python` fails before a single test runs.

**What no runner can prove** is the part that is about the hardware: hardware
HEVC decode, a gigabyte of RAM shared with Android, slow eMMC storage. Tools ->
Device report measures those on whatever box it runs on, and
`tools/deploy_android.py` puts the add-on there over adb. That step is by hand
and is still outstanding.

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
    python tools/subtitle_gate.py     the same releases and subtitle candidates
                                      scored by HEAD's matcher and the working
                                      tree's - the fit the picker shows, per
                                      engine; cached, so a rerun is free
    python tools/engine_gate.py       the same live releases scored by HEAD
                                      and by the working tree, films, series
                                      and anime - run it before committing a
                                      change to any source engine

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

## Where a press spends its time, and what was taken out

Measured on 6 October 2026 from a real Kodi's log and step by step outside
it. A picker opening took 7-19 s (9.5 typical), a chosen row 5-9.5 s to a
picture, an AI subtitle 6.5-31 s to its first line. What was cut, each one
checked to change nothing a viewer reads - the same rows, the same releases,
the same subtitle lines, the same file:

    rating subtitles      once per name, and only for releases that pass the
                          filters: Top Gun 11,547 ratings in 1.57 s -> 5,479
                          in 0.30 s (`matcher._title_contradicted`,
                          `scoring.rank(annotate=)`). Computation, so four to
                          eight times this on the projector.
    a provider hanging    three seconds more once Torrentio has answered, not
                          ten: 10.2 s -> 3.9 s (`AFTER_TORRENTIO`)
    the AI row            no file hash its candidates cannot match: the first
                          line is translated 0.3-1.8 s after choosing, not
                          3.2-6.1 s
    the link check        one hop unfollowed, Kodi given where it leads and the
                          file type so it does not probe (1.4 s of first byte
                          in the Top Gun log); the same file checked in the
                          last five minutes is not asked again, 0.9-1.4 s ->
                          0.02 s - in a window property, because the address
                          carries the debrid token

The next episode carries on in the same release, the same way. Releases
name every episode alike but for its number - "Hikaru.No.Go.TV.EP03.BluRay.
1080p.AC3.x264-CHD" and then EP04 - so the next-episode link names the file
that was playing and the subtitle route it was watched with, and `play._follow`
takes the cached release whose name agrees with its numbers taken out (90% or
more, same group, resolution, codec and source) and plays it without the
picker, translated if the last one was. Anything less alike opens the picker.
A chosen release that will not open is said aloud and the next one keeps the
chosen route: without it a fallback searched for Hebrew first, and the first
AI line came ten seconds after the picture instead of three.

Deliberately not done on the projector's account: starting the subtitle work
while Kodi opens the video, and reading the track inside the file while the
picker is open. Both save seconds by doing more at once, which is the load
pattern that ended Kodi POV IL on the same hardware. And `requests` against
`urlsession` is undecided until it is measured on the device: loading
`requests` costs every press, but `urlsession` opens a new TLS connection
per request, which is the expensive half on a Cortex-A53.

## Verifying the windows render

    python tools/check_windows.py     open the custom home window in Kodi and
                                      screenshot it into .kodi-test/shots/

Directory-mode tests never load a single line of window XML, so a malformed
control or a bad id shows only as a window that refuses to open. This opens
the real thing and photographs it.

That check found four defects nothing else could: `$LOCALIZE` does not resolve
add-on strings inside a Python add-on's own window (it needs
`$ADDON[plugin.video.pinky 32254]`), the row list was 90px too short so the
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

A later pass added one that only a remote can find, and the fix for it turned
out to be a navigation change rather than a code one. **The details screen's
buttons could not read the episode list's cursor**, and not because it was
read badly: getting from episode nine back up to the button row meant pressing
Up nine times, and every one of those presses walks the selection up with it.
By the time a button had focus the list genuinely *was* on episode one. The
cursor was right; the route was wrong.

So the list now has `onright` and `onleft` pointing at the button. Sideways
leaves in one press and the cursor stays where the viewer left it, and on a
vertical list neither key did anything else, so nothing was taken away. With
the cursor trustworthy the button acts on the highlighted episode - and
**says so**: its label is a window property rebuilt on every move, reading
"Choose a source   1x05". A button that acts on something the viewer cannot
see is one they have to press to understand. Asking which episode survives as
the fallback for when nothing is highlighted.

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
  completes, Kodi navigates, "stay in Pinky" reopens it, and Kodi deadlocks
  during its own shutdown. Eight plugin invocations and a stuck process.

The window is therefore not opened from a directory call at all. The directory
is ended at once and `RunPlugin` asks Kodi to run the plugin again with no
directory attached; `handle` is -1 in that second invocation, which is how the
two are told apart. Start-up and "stay in Pinky" call `RunPlugin` directly for
the same reason, rather than `ActivateWindow(Videos,...)` - the window is not a
directory, and routing through the video browser left an empty plugin folder in
the back stack. Measured after: one invocation, no busy dialog, and Quit exits
in 2.0 seconds.

And one about ids, which cost the source picker on every episode in the
add-on. **An episode's TMDB id is not its show's** - Silo's first episode is
2964686 where the series is 125988 - and every route below the context menu is
keyed on the series. `listing.context_menu` passed the episode's own id, so
`build_meta` asked TMDB for a show that does not exist, got no title, and gave
up: the press did nothing whatsoever. On a film the id is the right one, which
is why it looked like "choosing a source only works on films". The details
window had known this all along and read `extra["tmdb_show"]`; the context menu
had not.

Two more from the first real installation, and both had passed every test
here because the stubs are tidier than Kodi is.

* **`kodi.addon_path()` comes back with a trailing separator.** The stub's
  does not. So `target + ".old"` named a file *inside* the folder being
  replaced instead of a sibling of it, and `os.path.dirname` of the same path
  returned the add-on folder itself - meaning the staging directory was
  unpacked inside the thing it was meant to replace. `OSError: [WinError 87]`,
  and the first update anyone ever tried failed. `normpath` fixes both, and
  the test now passes a path shaped like the real one.
* **A `.po` entry needs a blank line before it.** "Kids mode" sat in the file
  and every check here found it, because they all look for `msgctxt` with a
  regex - but a real parser folds an entry written straight after the previous
  `msgstr` into the one above, so the id never registers and Kodi renders the
  number. The menu read `32226`, and an unlabelled row in a menu is one
  somebody presses to find out what it does. This one replaces the entire
  catalogue with the kid-safe version, which is how kids mode came to be
  switched on twice in an evening.

And one that only search could reach. **A VOD entry is a programme, not a
stream.** `listing.target_url` marked every one of them playable, so Kodi
asked a directory route for something to play, the route answered with a
directory, and nothing happened - fifty-two episodes of the programme in
question, none of them reachable. It never showed while browsing because the
VOD screens build their own directories; only search sends items through
`target_url`. Channels share the type and genuinely are streams, so the url is
what tells them apart.

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

It answers for a stock clip too, and that is not the film. Torrentio's
`/resolve/` link for a torrent the debrid service does not hold adds it there
and redirects to `/videos/downloading_v3.mp4` - a green screen reading
"Torrent is being downloaded to debrid...", which played as Hikaru no Go 1x03
on 6 October 2026 with an AI translation running over it. A link whose final
address is a provider's `/videos/*.mp4` is now one that will not play, and the
next source is tried (`play._placeholder`).

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
has no `userdata/addon_data/plugin.video.pinky/settings.xml` at all, and
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
     70   the right series and the right episode
     55   the right title and the same source
     40   the right title and nothing else
      0   demonstrably the wrong episode

**The episode rung was missing and it cost anime everything.** An anime
episode's picker drew *every* row at 52%, which is this arithmetic and not an
opinion: 40 for the title, 12 for the episode, and for anime there is no
third term. A subtitle called "Attack on Titan - S01E12 - Wound" carries no
group, no source, no resolution and no codec to agree with a release called
"[Leopard-Raws] Shingeki no Kyojin - S01E12" - the two facts it states are
the only two that can score, and one of them was worth twelve points.

The number is documented as a probability that this subtitle fits this file,
so it was checked against two rulers. Against a hash-matched reference,
candidates scoring under 55 measured a fit of 0.64 and **0.78 after
re-timing**. Against a second, differently-named upload of the same episode -
which is the only corroboration available for anime, because exactly one
provider answers for it - they agreed **0.75**. Three quarters, not one half.

So naming the right episode of the right series is worth 30 and lands on the
threshold. It changes no ordering, because every candidate for an episode
either names it or is zeroed by the wrong-episode penalty. What it changes is
what the threshold decides: at 52 a correct English subtitle was *below*
threshold, so an AI translation was preferred to it - which on Hikaru no Go
meant a perfect file being handed to a model that was out of quota while the
episode played with nothing.

Measured after, against real release names so the matcher had a group, a
source and a resolution to agree with - six titles per group, counting how
many reach the threshold:

    group     was >= 70   now >= 70
    anime        2/6         5/6
    foreign      3/6         5/6
    series       6/6         6/6
    film         4/6         4/6

Exactly where it was needed and nowhere else: films have no episode to name,
so nothing moved them, and English-language series were already clearing.

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

**The automatic path is strictly Hebrew, then AI, then English**, each tried
to the end before the next. Hebrew: the track inside the file, one saved from
an earlier play, the best downloaded one that fits, then the best there is
below the threshold - a Hebrew subtitle somebody made for this title comes
ahead of one a model makes, which reverses what this did before. AI: from the
source that fits best, then from anything at all. English: the file's own
track, which is how every other anime add-on shows subtitles at all, then a
downloaded one - which without an AI engine was never used, so a film with a
good English file and no Hebrew played with nothing.

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

The order is Arabic, then any other gender-marking language (French, Spanish,
Italian, Russian...), then English and Turkish, then Japanese, Korean and
Chinese - Arabic because it is the closest to Hebrew and what POV itself uses,
the East Asian three last because they drop the subject so often that the
model has to guess who is speaking. It is a bonus on the match score
(`source_bonus`), not a strict order, because a translation keeps its
source's timings: an Arabic file for a different cut becomes a well-gendered
Hebrew subtitle that is minutes out.

**Source selection assumes the LLM, but only pays for it when Hebrew does not
fit.** With a translation engine configured, a release whose only
well-fitting subtitle is in another language is shown as "AI subtitles" and
ranked above a release whose Hebrew does not fit - never above Hebrew that
clears the threshold. What it translates from is searched in a *second*
round, and only when needed: when a film starts, once there is no usable
Hebrew subtitle at all; in the picker, once no release has Hebrew that fits.
That round asks for **Arabic, English, Polish, Spanish, Russian and French**,
plus the show's own language when it is Chinese, Korean, Turkish, Japanese or
Italian - a Turkish drama is asked for Turkish, an American film is not asked
for Korean. Choosing AI translation by hand asks for every language. Without
an engine none of this is requested, because every language is another
request per provider.

It used to be Arabic and English alone, while `context.GENDER_MARKING` listed
nineteen languages that were never once asked for - so the bonus below could
only ever choose between the two that were. Measured over five titles on 24
September 2026, subtitles found per language:

    en 29   ar 32   pl 26   es 20   ru 20   fr 18   pt 15   it 12

and the anime rows are the whole argument, because that is where English and
Arabic are thinnest: Naruto Shippuden 8x14 has **Polish 6 and Russian 4**
against English 2 and Arabic 3, and Hikaru no Go 2x02 has exactly two
subtitles in the world, one of which is Polish. Italian and Portuguese are
left out on the same numbers - same request, least found, and a list that
grows without a reason is a search a device waits longer for. The one thing
that does not work and would make this free is asking for several at once:
`sublanguageid-eng,spa` answers **400**.

When all of them find nothing, a second round asks a wider set - German,
Portuguese, Italian, Dutch, Turkish, Czech, Hungarian, Romanian
(`auto.WIDER_SOURCE_LANGUAGES`) - and only then: Sabrina, the Teenage Witch
season 6 has German, Portuguese, Swedish and Serbian and nothing else, and
the picker had offered no way to Hebrew at all.

Widening it exposed a duplicate: a French film was asked for French twice,
once from the base list and once as its own language, which is two identical
requests per provider and the same file offered twice in the picker.

**The engine is a setting, and one of them is free.** Gemini, OpenRouter or
any OpenAI-compatible endpoint. OpenRouter is the interesting one: it speaks
the OpenAI protocol, so it is a preset - an address, two identifying headers
and a model name - rather than a second client, and its free catalogue removes
the cost argument against translating every subtitle that does not fit. Two
things the free models do that the hosted ones do not: several answer **HTTP
400** to any request carrying `response_format`, so it is asked for once and
then dropped, and an exhausted model arrives as a **200 with an error object**,
which would otherwise read as a subtitle with nothing in it. The model name is
a setting because OpenRouter's free list changes; the default is a starting
point, not a promise.

**A translation that is being watched has to arrive, and say so while it
does.** Watched in a real Kodi on 6 October 2026, with Gemini's full model
answering 503 "high demand" all day (and once holding a request past 150 s),
while flash-lite translated 80 lines in four seconds. Naruto Shippuden 3x55
showed nearly four minutes with nothing on screen saying anything was
happening, then "The translation did not finish" - over the next episode,
because the job had kept retrying after the viewer left it. Hikaru no Go
then got 40 Hebrew lines and English after them. What changed:

* **Kodi draws no background progress bar over a playing video** -
  photographed at 12, 45 and 110 seconds into a translation, nothing in the
  corner. So `auto._Status` also says each stage as a notification: finding
  a source, reading the track inside the file, downloading, "140 of 349
  lines translated from English". Photographed after: every stage on screen.
* **Every chunk goes to the fast model first**, so the whole episode is
  Hebrew in about half a minute, then `gemini.full` goes over it and only its
  answers replace the quick ones; a busy full model ends that pass and costs
  nothing. Measured after: 343 of Hikaru no Go's 349 lines in Hebrew, against
  40.
* **A busy model steps back for ten minutes** (`gemini._BUSY`) instead of
  being asked first for every chunk, a request it holds open is cut at a
  minute when there is another model behind it, and an engine refusing
  everything hands over at once (`TranslationRefused`) rather than after each
  chunk is refused in turn.
* **From the line being watched**, then on to the end, then back for the
  start - the job that began nine minutes in used to translate the opening.
* **Google Translate's free endpoint is the last engine** (`google_web`),
  and the first when no key is set: no account, lines sent many to a request
  and used only when exactly as many come back - 833 lines in under five
  seconds, every batch whole. It knows nothing of the cast, so gender is
  guessed; it is what Kodi POV IL and DarkSubs fall back to, and it is Hebrew
  where the alternative was English. The tests point it at nothing, so a
  failing fake engine cannot pass by reaching Google.
* A superseded job says nothing.

**No Gemini model name here may contain a version number.** Pinning has
failed twice: `gemini-2.5-flash` began answering 404 to every request from a
new key in September 2026, and the `gemini-3.6-flash` that replaced it did
the same for a real key two days later - both times while every test in this
repository passed, because a fixture never asks Google anything. The chain is
`gemini-flash-latest` then `gemini-flash-lite-latest`, and a test fails on any
name with a digit in it.

Flash first and flash-lite second is the quality order, not the allowance
order. Measured free tier, September 2026: flash is 10 requests a minute and
250 a day, flash-lite is 15 and **1,000**. Flash-lite is four times the daily
allowance and half again the speed and is still second, because it was
measured getting Hebrew gender wrong where flash gets it right - which is the
one thing this whole file exists to avoid. They are separate quotas, so a
spent flash is not the end of the film. Pro left the free tier in May 2026 and
answers 429, so it is never in the chain.

**404 and 503 are opposite claims and were treated as one.** 503 says the
model is there and busy; 404 says the name is not served on this endpoint,
and it will say so again for every chunk of the film. Measured on a real
translation: the configured model answered 404 and was asked again for every
chunk, three requests and two retries deep each time, for over a minute of an
already-playing episode. A 404 now strikes the model off for the session; a
429 or a 503 keeps its place.

Chunks are **120 lines** by default, 200 and 300 on the richer profiles. The
old 50 was a memory decision, and the memory it saves is a few kilobytes of
JSON against a 6 MB artwork budget - while fewer, larger requests is exactly
what a model with a daily request allowance wants, and gives the model more of
the scene to translate in context. The request budget grows with the chunk
count so a split still has room, but only by half of it: the cap exists so one
misbehaving model cannot turn a film into hundreds of requests.

**The picker opens on three lists, as a comparison.** Up to ten releases
ranked by their best Hebrew subtitle (NATIVE, blue), up to ten by the best
subtitle AI can translate from (LLM, yellow, naming the source language), and
up to five by their best English subtitle (ENGLISH, red); the same release can
be in more than one. It is more than the "top-K only" eight on purpose: the
point is to see which way of getting Hebrew works, and twenty-five list rows
cost nothing next to artwork. The row chosen decides playback - LLM translates
without searching for Hebrew, NATIVE and ENGLISH use that language and never
turn into AI. Measured on real searches: 5 of 7 anime and both Turkish dramas
tried had no Hebrew subtitle at all and a full LLM list, which is the case the
page exists for. In the player's own subtitle list AI translation is offered
twice, into Hebrew and into English, rather than once in whatever language
Kodi's subtitle setting names - English, on a stock Kodi.

**There is no autoplay any more, and no setting that brings it back.**
`sources.autoplay` is deleted rather than defaulted off, because the ranking
cannot see what this page shows: the three lists are a question about
*subtitles*, and the source sort never asks it. For most anime and every
Turkish drama measured the first list is empty - so starting the top row by
itself threw the comparison away on exactly the titles it was built for. A
setting that must never be true is not a setting.

That removed the details screen's second button with it. "Choose a source"
and Play now did the same thing from the same list position, so Play absorbed
the episode indicator - the part worth keeping - and with the label naming an
episode it has to act on the highlighted one or the label is a lie. On the
season list, where nothing is highlighted and the label says only "Play", it
still means the next unwatched episode.

**Taken: progressive delivery.** Translating a feature film takes minutes.
Showing each finished chunk as it arrives means the viewer starts watching
almost immediately. Kodi caches a subtitle by path, so the file name alternates
between two slots to force a re-read.

**Not taken: extracting embedded subtitles from a remote file.** (Reading
only the *track list* is taken, and is cheap - see "the tracks inside the
file" above; this is about pulling the cues out.) They ship an
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

* **Trakt and Premiumize need an application registered before anybody can
  sign in.** Both have the same "open a link and it connects on its own"
  device flow that Real-Debrid and AllDebrid have, and for those two it works
  out of the box because Real-Debrid publishes a client id for open source
  apps and AllDebrid's PIN flow needs nothing but an agent name. Trakt wants a
  client id *and* secret, Premiumize an OAuth client id, and neither publishes
  one - so the first thing our flow did was open a keyboard for two long
  strings, which is the exact barrier the sign-in screen exists to remove and
  meant nobody ever reached the link. `trakt.BUNDLED_CLIENT_ID` /
  `BUNDLED_CLIENT_SECRET` and `premiumize.BUNDLED_CLIENT_ID` are where those
  go, the same way `tmdb.BUNDLED_KEY` does. They are empty until somebody
  registers the applications, and until then the flow still asks. An
  application credential in a public repository is worth nothing on its own -
  every token still needs a viewer to approve it on the service's own site.

* Ktuvit is implemented against its documented flow and tested against
  fixtures, but has never signed in to a real account.
* MDBList is implemented and fixture tested; the live API needs a key.
* AI subtitle translation is fixture tested; the live path needs a Gemini key.
  Everything around it *has* been run in a real Kodi 21 over a real playback:
  the row appears in Kodi's own subtitle dialog in Hebrew, pressing it reaches
  the background service in about 170 ms, the subtitle list closes itself and
  the key prompt opens over the still-playing film. What no key can prove is
  the only thing left - that the model returns usable Hebrew.

  Run with a real key on 24 September 2026, and it did not get that far: the
  free tier answered **429 and 503 for an hour**, so the request path is
  proven and the Hebrew still is not. The two defects that run exposed - a
  pinned model name and a 404 retried for every chunk - are fixed above and
  were both invisible from a fixture.
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

* **BSPlayer's hosts stopped answering.** Measured 24 September 2026: all
  three of `s1`, `s2` and `s3.api.bsplayer-subtitles.com` resolve to one
  address, 185.100.234.211, and none of them accepts a connection on port 80.
  DNS is alive and the server is not.

  It was costing far more than nothing. Three hosts at a four second connect
  timeout is twelve seconds per call on one of four shared workers, and its
  own budget was `MAX_CALL_SECONDS = 12.0` against a **ten second** search
  deadline - so one call could outlive the search that asked for it. That is
  what `deadline hit after 10.0s, dropped: ktuvit` in the log was really
  about: not Ktuvit being slow, but a dead provider holding a worker past the
  deadline and taking whatever queued behind it. It ships **off** now, says so
  once per process if switched on, and its budget is six seconds so it can
  never outlast the search again.

  This matters more than one provider going quiet, because it was the *second*
  hash provider. `reference_cues` accepts a hash match and nothing else, so
  with OpenSubtitles alone the only ruler the add-on owns has one supplier.

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
