# Continuation report — 22 September 2026

The handoff for continuing Katan on another development host. Branch:
`development`, level with `origin/development` at the time of writing. **No
release was created and no version tag was pushed** — the only tag is
`v0.0.1`, which exists locally and is deliberately not on GitHub yet (pushing
a `v*` tag runs `release.yml` and publishes). `python tools/release.py
--dry-run` offers `v0.0.2`.

Read `CLAUDE.md` first, then `AGENTS.md`, then this file. `SUBTITLE-LOG.md`
has the subtitle measurements and the release procedure; `NIGHT-LOG.md` has
the earlier real-device work.

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

* **1,868 tests pass** (`python -m pytest tests`, Windows, Python 3.11, about
  85 s). `python tools/build.py --check` is valid.
* No e2e or GitHub workflow was run, per the standing instruction.
* Nothing in this batch has been watched in a real Kodi; all of it is logic
  proven against the stubs and, where marked, against live services.

## What this batch changed, and how each was checked

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

## Known gaps and next work

1. **Make the repository public, set Pages to GitHub Actions, and cut a
   release** (`python tools/release.py`, then push the tag). Until then no
   device can install or update. Decide separately whether `v0.0.1` goes up
   as a historical tag — pushing it also runs `release.yml`.
2. **Clean `Application.Quit`** — still broken: the Katan window's plugin
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
  "No PVR add-on enabled", Backspace walked out of Katan. The box is now an
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
