# Continuation report — 24 September 2026

The handoff for continuing Pinky on another development host. Branch:
`development`, level with `origin/development`. **No release was created and
no version tag was pushed.**

Read `CLAUDE.md` first, then `AGENTS.md`, then this file. `SUBTITLE-LOG.md`
has the earlier subtitle measurements and the release procedure;
`NIGHT-LOG.md` has the earlier real-device work.

**This batch is different in kind from the ones below it.** Almost none of it
was found by reading code. It was found by running against live services and
reading Kodi's own log beside ours, and four of the defects had been shipping
invisibly because the path that exercises them is the path no test takes. If
you continue this work, continue it that way.

## Before anything else on a new host

* **History was rewritten again on 22 September, and the repository was
  re-created.** Every commit now has one author identity, the personal one,
  and no co-author trailer. A clone from before that date shares no commit
  ids with this one: **delete it and clone fresh** — pulling would merge the
  two histories and bring every commit back twice under the old identities.
* **Set the commit identity before the first commit.** On a machine signed
  into Windows with a work account, Git for Windows silently takes the work
  sign-in as the author when `user.email` is unset — that is how the wrong
  address got into the history. Set `user.name`/`user.email` for this repo
  (or an `includeIf gitdir` rule for the folder) and check with
  `git var GIT_AUTHOR_IDENT` before committing.
* **The repository is private for now.** Kodi fetches anonymously and GitHub
  Pages on a private repository needs a paid plan, so until it is made public
  and Pages is set to "GitHub Actions", `noflevi.github.io/kodi` answers 404
  and no device can install or update. The address is unchanged, so installed
  devices recover as soon as a release is published there.
* **Hosting is GitHub Pages, not Cloudflare.** Releases publish to
  `noflevi.github.io/kodi`, the in-add-on updater reads
  `github.com/NofLevi/kodi/releases/latest/download/addons.xml`, the test
  channel is the `test-channel` branch, and publishing needs no secrets.
* **Not in git, copy by hand if wanted:** `subtitles.jsonl` and
  `.survey-subtitles-2026-09.jsonl` (the subtitle surveys), `split.jsonl` (the
  picker survey), `.kodi-test/` (`python tools/setup_kodi.py` rebuilds it).

## Current verification state

* **1,980 tests pass** (`python -m pytest tests`, Windows, Python 3.11).
* No e2e or GitHub workflow was run, per the standing instruction.
* **Much of this batch was watched in a real Kodi**, which is new: the source
  picker, the episode walk into Mako, Escape pausing, and the subtitle search
  were all read out of `kodi.log` during playback rather than inferred. What
  has still never been seen is a Gemini translation succeeding - the free tier
  answered 429 and 503 for the whole session.
* **One number in this file is contaminated and says so where it appears:**
  the download half of the accuracy survey measures an OpenSubtitles quota I
  exhausted, not the add-on.

## What this batch changed, and how each was checked

Eight commits, `6a5f3f9..1ae97a3`. **1,980 tests pass** (`python -m pytest
tests`, Windows, Python 3.11).

### The four defects that were invisible by construction

* **Ktuvit had never signed in on a device.** `urlsession` exposes headers as
  an `email.message.Message`, whose `.get()` returns only the **first** header
  of a repeated name - and Ktuvit sets `ASP.NET_SessionId` first and `Login`
  second. A host with `requests` installed read the cookie jar and worked;
  every shipped Kodi read the session id, found no login, and logged "ktuvit
  refused the sign in" against a good account. Every earlier claim in
  `CLAUDE.md` about Ktuvit working was measured through `requests`.
  *Fixed at the root:* `urlsession.Response` has a `cookies` dict built from
  every `Set-Cookie`. *Checked:* the real login with `requests` forced off
  returns `SIGNED IN`; the two-header case is a regression test. Same shape as
  the gzip bug in `_case_insensitive`, and it will recur.
* **BSPlayer's hosts are gone, and it was taking Ktuvit with it.** All three
  resolve to 185.100.234.211 and none accepts a connection. Its budget was
  `MAX_CALL_SECONDS = 12.0` against a **ten second** search deadline, on one
  of four shared workers - so `deadline hit after 10.0s, dropped: ktuvit` was
  never about Ktuvit. Ships off, budget six seconds, and a test fails if any
  provider may outlast the deadline. *This was the second hash provider*:
  `reference_cues` takes a hash match and nothing else, so the only ruler the
  add-on owns now has one supplier.
* **An IMDb id that finds nothing is not evidence of nothing.** An upload is
  filed against an id only if the uploader said so. `imdbid-0426711` knows
  English for Hikaru no Go episodes 1-30 and stops; the same episode asked
  *by name* returns `Hikaru No Go S01E32.en.vtt`. That answer was then
  discarded because its `SeriesIMDBParent` is 13364846 - the show has three
  IMDb entries. The name query runs only after the id query is empty, and is
  not judged against the id it deliberately went around. *Measured:* Hikaru no
  Go season two, 0 of 8 episodes with English to **6 of 8**; candidate
  coverage across the survey 85% to **93%**.
* **A MicroDVD file whose first line is an advert parsed to zero cues.** The
  frame-rate declaration had to be the first meaningful line, and release
  sites put a banner there. Detection is by shape now, and an undeclared rate
  reads at 23.976 - which `sync.py` can correct and `verify_and_sync` can
  refuse. Returning nothing cannot be corrected.

### The measurement tool was answering a different question

`accepted` was `chosen_score >= threshold` - the matcher's opinion of a
filename, not what the add-on does with it. `verify_and_sync` rejects what a
hash reference disproves and what stops too early, then falls through to the
next candidate, and the tool ran none of it. It reported a file covering 26%
of its runtime as accepted. It now runs the real decision and reports both;
over 27 titles they disagree, 50% against **83%**.

### The episode rung was missing from the score ladder

Every row of an anime picker read 52%, which is arithmetic: `WEIGHT_TITLE 40
+ WEIGHT_EPISODE 12`, and for anime there is no third term - a subtitle named
`Attack on Titan - S01E12` has no group, source, resolution or codec to agree
with `[Leopard-Raws] Shingeki no Kyojin - S01E12`. Against a hash reference
such candidates fit 0.78 after re-timing; against a second upload of the same
episode they agree 0.75. `WEIGHT_EPISODE` is 30, so title-plus-episode lands
on the threshold. *Measured with real release names:* anime 2/6 to **5/6**
over the threshold, foreign drama 3/6 to **5/6**, series and films unchanged
- they have no episode to name. It changes no ordering: every candidate for
an episode either names it or is zeroed.

### Where this is actually weak, and the number that was wrong

Hebrew, then a language the model can work from, seven titles a group:

    group        has Hebrew   translatable   neither
    anime            0/7          2/7           5
    turkish          0/7          2/7           5
    korean           0/7          3/7           4
    us series        4/7          4/7           3
    israeli          3/7          3/7           4
    films            6/7          7/7           0

Both Hebrew providers were checked on the same run against titles that
certainly have Hebrew - Fight Club 26 and 24, Breaking Bad 9 and 7 - so the
zeros are the corpus, not the code. The sample is *currently popular* titles,
which skews to episodes that aired days ago; a finished season does better.

**The "neither" column overstates it, because none of these numbers can see
inside the file.** `embedded.py` asks Kodi, so it answers only during
playback. Read out of the Matroska header over two ranged requests, **five of
eight anime episodes carry a subtitle track** (`S_TEXT/UTF8`, `S_TEXT/ASS`).
A track inside the file is in time by construction. The real gap is the
picker, which cannot promise a track it has no way to see.

### Smaller, all measured

* **A failed translation shows the subtitle it was made from**, in a language
  the viewer reads. Hikaru no Go 1x02 found the exact release name in English,
  timed it, handed it to Gemini, got 429, and played with nothing.
* **`subs.ai.engine` is a preference, not the whole answer.** `engines()`
  returns the rest behind it, because for this catalogue the translation *is*
  the subtitle and an engine out of quota is a blank screen. A cancelled
  translation never moves on.
* **No Gemini model name may contain a version number** - pinning failed
  twice. The chain is `gemini-flash-latest` then `gemini-flash-lite-latest`,
  and a test fails on any digit. 404 (retired) strikes a model off for the
  session; 429 and 503 (busy) do not. Free tier measured: flash 10/min and
  250/day, flash-lite 15 and 1,000 - flash-lite is still second because it was
  measured getting Hebrew gender wrong.
* **The AI source languages were two of the nineteen we knew about.** Now
  `ar, en, pl, es, ru, fr`; all but English mark gender. Per-language counts
  are in `CLAUDE.md`. Widening it exposed a French film asked for French twice.
* **The hash wait was 5 s and the hash took 5317 ms.** Now 8 s. Missing it is
  not one provider short: without a hash nothing comes back marked `hash`, so
  there is no timing reference for anything.
* **A translation source is re-timed before it is translated** - both paths
  previously said in as many words that timings are never touched.
* **Escape pauses rather than keeps playing**; **Continue Watching works
  without Trakt** (it was `needs=("trakt",)` and could never appear); **two
  presses racing** could start the cancelled title; **VOD is asked before the
  trackers** and walks down to the episode; **autoplay is deleted** rather
  than defaulted off.

## Known gaps and next work

1. **Re-run the accuracy survey.** Today's is contaminated: half way through,
   `rest.opensubtitles.org` began refusing *downloads* - search still answers -
   after three surveys in one afternoon. All 93 parse failures were `download
   returned nothing`. Search-side numbers stand; download-side ones measure
   the quota. Wait a day, then
   `python tools/survey_subtitles.py --count 400 --debrid`.
2. **A 429 is retried within a request and never remembered between them.**
   TorrentsDB is 429ing right now, from today's load, and is asked on every
   search, costing a worker each time. The fix is a short per-host cooldown in
   `http` honouring `Retry-After` - the same shape as the Ktuvit refusal cache
   and the BSPlayer deadline. **Not built.**
3. **The picker cannot see embedded tracks**, and five of eight anime files
   carry one. Reading the Matroska header costs two ranged requests, which is
   what the hasher already spends. Worth considering; not built.
4. **`test_the_channels_that_remain_all_resolve_to_a_url` failed once** in a
   full run and passes consistently alone and in its own module - an
   order-dependent flake on a Russian radio channel. Unrelated to this batch,
   real, not chased.
5. **An OpenRouter key would do more than anything left here.** Hebrew does
   not exist for anime or Turkish drama, so translation reliability *is*
   subtitle accuracy, and Gemini's free tier is exhausted for hours at a time.
   The engine fallback is built and waiting for a second engine.
6. Still open from before: make the repository public and cut a release,
   `Application.Quit`, and **physical validation on the U4 projector and the
   Mi Box**, which is what this add-on exists for.

## Previous batch — 22 September 2026

### The automatic subtitle order is strictly Hebrew, then AI, then English

* **Hebrew:** the track inside the file, one saved from an earlier play, the
  best downloaded one that fits (70+, verified against a hash reference when
  there is one), then the best there is *below* the threshold. That last step
  used to come after AI translation; a Hebrew subtitle somebody made now wins
  over one a model makes. *Deliberate reversal, the owner's decision.*
* **AI:** the second search for sources (Arabic, English, plus the show's own
  language for zh/fr/ko/es/it/tr/ja), translation from the best fit, then the
  last-resort translation from anything.
* **English:** the file's own track, then a **downloaded English subtitle**.
  Before this, without an AI engine, a film with a good English file and no
  Hebrew played with nothing, and an anime episode with English inside the
  file played with that track switched off.
* *Checked:* new pipeline tests for each part, and the full suite.

### The picker's three lists recoloured

* NATIVE Hebrew **blue** `FF6CB8FF`, LLM **yellow** `FFFFD23F`, ENGLISH
  **red** `FFFF7373`, chosen by contrast ratio against the picker's real row
  textures (at least 4.5:1 focused, 7:1 plain), with a black label shadow for
  a bright frame behind the window. *Not yet seen in Kodi.*

### Two episode-matching fixes, for every provider

* `S01E66` is the 66th episode counted from the first — how Hikaru no Go's
  TMDB 3x06 is filed — and was refused as the wrong episode.
* A stated season now has to agree: "Oshi no Ko S3 - 06" was offered for
  season one's episode six. These also decide which file plays from a torrent.

### Anime subtitles: Jimaku built, measured, not shipped

* Over 1,000 anime shows, Jimaku had about 58% of episodes and was the only
  translatable subtitle for about one in six. Found by AniList id (via ARM,
  with Kitsu episode counts for split seasons), 0 wrong over 143 replayed
  episodes. **Dropped** by the owner: it only matters with AI on, Japanese is
  the weakest source for Hebrew, and it costs a provider, a mapping and a key.
  The record is in `CLAUDE.md`. Kitsunekko was rejected (5% as single files).
* Research into a4kSubtitles, the OpenSubtitles.com add-on, the Stremio
  protocol and the Stremio Jimaku add-ons: none of them re-times subtitles,
  and for anime they rely on the English inside fansub files — which is what
  the new English part of the order now does.

### The subtitle survey was re-run with real streams

`tools/survey_subtitles.py --count 400 --debrid`, 350 titles:

* 86% of titles with sources had a subtitle candidate; none claimed the wrong
  language; the name-score threshold of 70 let nothing badly fitting through
  (median fit 0.93 at 70-89).
* **`fit_segments` keeps its place:** 1 of 12 hash-referenced titles needed a
  split (a Supernatural special, fit 0.64 on one offset, 0.78 on two). The
  kill criterion was under 5%.
* A zero for Fight Club late in the run was transient; asked again it returns
  26 Hebrew and 24 English candidates.

### Hardening

* `subs/embedded.py` no longer crashes when Kodi's JSON-RPC answer is not the
  expected shape, for both subtitle and audio streams.
* `.gitignore` regained two broader rules that had narrowed over time:
  `.env*` and `settings.local.*`.

### Known gaps as of 22 September

1. **Make the repository public, set Pages to GitHub Actions, and cut a
   release** (`python tools/release.py`, then push the tag). Until then no
   device can install or update. Decide separately whether `v0.0.1` goes up
   as a historical tag — pushing it also runs `release.yml`.
2. **Clean `Application.Quit`** — still broken: the Pinky window's plugin
   invocation does not honour Kodi's abort.
3. **Watch an AI translation end to end** with a real Gemini key.
4. **See this batch in Kodi:** the picker colours, and an anime episode with
   no Hebrew switching on its own English track.
5. **Proposed, not built** (the owner to choose):
   * search Spanish, French and Russian too in the automatic AI round, so a
     gender-marking source is fetched when there is no Arabic;
   * SubDL and Podnapisi as English providers (what a4kSubtitles uses beyond
     OpenSubtitles and BSPlayer);
   * ask OpenSubtitles by the episode's own IMDb id — measured +4% of anime
     episodes overall, +13% of popular ones.
6. **Retire the AnimeTosho *source* provider before October 2026**, when its
   feed server shuts down; Nyaa and Torrentio already cover anime.
7. Still open from before: UI worker lifecycle in Search/Home/Auth, update
   authenticity (signed manifest), Pastebox is plain LAN HTTP, kids PIN
   lifecycle, device capability filtering, and **physical validation on the
   U4 projector and the Mi Box**, which is what this add-on exists for.

## Previous batch — 17 September 2026

Commit ids from that report no longer exist after the rewrite and have been
removed; the descriptions stand.

### Fixed — found in a real Kodi 21 on Windows

* **Every OpenSubtitles search failed on a real device**. The
  stdlib HTTP path flattened response headers into a case-sensitive `dict`;
  OpenSubtitles sends `content-encoding: gzip`, so gzip bytes reached the JSON
  parser and every language logged "did not answer". Machines with `requests`
  never saw it. Responses now keep `email.message.Message`, case-insensitive
  for every header. *Checked:* live on the no-`requests` path, Hikaru no Go
  1x05 now returns subtitles; regression test added.
* **The search box could not be typed into on a keyboard**. Kodi
  21's `Action` has no character, so keys ran the keymap — one letter opened
  "No PVR add-on enabled", Backspace walked out of Pinky. The box is now an
  `edit` control, focused on open. *Checked in Kodi with real keystrokes:*
  "hikarx", Backspace, "u" gave "hikaru" with 5 suggestions; the log shows
  keyboard mode. Return arrives as Select (7), which opens Kodi's modal
  keyboard on an edit control — with a query typed it is closed and the search
  runs. *Not fully confirmed:* the Enter-to-search path, because the test
  harness could not reliably keep Kodi in the foreground.
* **The Gemini key field was hidden at Expert level**; now shown at
  every level under AI translation.

### Subtitles and AI translation — logic tested, not yet watched in Kodi

* **AI-native source selection**. With a translation
  engine configured, a release whose best-fitting subtitle is in another
  language shows "AI subtitles NN% (estimate)" and ranks above a release whose
  Hebrew does not fit — never above Hebrew that clears the threshold. The
  translation sources are searched only in a *second* round: at playback once
  no Hebrew fits (or the one that looked right fails its hash/coverage check),
  in the picker once no release has fitting Hebrew. That round asks Arabic and
  English plus the show's own language when it is zh/fr/ko/es/it/tr/ja.
  Choosing AI translation by hand asks every language.
* **Source-language preference** is a bonus on the match score: Arabic (+25,
  POV's choice and closest to Hebrew), other gender-marking languages (+18),
  English and Turkish (0), Japanese/Korean/Chinese (−10). A bonus, not an
  order, because a translation keeps its source's timings.
* *Checked live without an LLM call:* Hikaru no Go 1x05 finds Arabic, English
  and Japanese subtitles at equal fit, and translation would start from the
  Arabic. **Never checked:** that Gemini returns good Hebrew. That is the first
  thing to watch on a real playback.

### A title's own language

* **It was being lost for every series.** `build_meta` put the episode in
  `meta["item"]`, and TMDB episodes carry no language, so the ranking rule that
  a Turkish release of a Turkish drama is the honest one never fired, and a
  Turkish-audio release took the −400 wrong-language penalty. Now set once on
  the meta from the film or series. *Checked live:* Hikaru no Go `ja`, Game of
  Thrones `en`, Parasite `ko` (all blank or unreachable before).
* A **Hebrew-language title** gets no automatic subtitle, and the picker shows
  "Hebrew audio" without searching.
* A **Turkish, Spanish and Italian dramas** row on the Series tab
  (`with_original_language=tr|es|it`, measured to return all three).
* **The source sort gained two terms.** `_unwatchable` puts a release only in
  an unreadable language *before resolution* — as the −400 weight it sat after
  size in the sort key and only ever broke ties, so a higher-resolution Italian
  dub beat an English release. `_dubbed` puts a dub below the original audio
  right after resolution when the show's language is not one the viewer reads;
  kids mode is exempt, the dub stays listed, and a bare MULTI counts as DUAL.
* **Audio track selection**: on a file with several audio languages, playback
  switches to the original language, or to Hebrew in kids mode.
* *None of these four have been seen in Kodi yet.* The row, the "Hebrew audio"
  badge, the dub ordering against real sources and the audio switch all need
  a look.

### Documentation

* `CLAUDE.md` and `SUBTITLE-LOG.md` rewritten for GitHub Pages (they had been
  find-and-replaced into sentences like "Cloudflare Pages publishes at an
  unguessable pages.dev address - noflevi.github.io/kodi").

## Safety and release state

* No credentials, signed URLs, cookies, entitlement tickets, private request
  data or work identities belong in this report or any tracked file. Before
  the 22 September push every tracked file and every version in history was
  scanned for tokens, private keys, API keys and email addresses: the only
  real key is `tmdb.BUNDLED_KEY`, a deliberately public read-only catalogue
  key, and the only addresses are `example.com` fixtures.
* A future `AGENTS.md` must stay source-only and outside generated ZIPs and
  `repo/`.
* Pushing `development` does not publish. Do not create or push a version tag
  until the owner explicitly asks for a release.
