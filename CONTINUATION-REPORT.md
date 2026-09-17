# Continuation report — 17 September 2026

The handoff for continuing Katan on another development host. Branch:
`development`, level with `origin/development` at the time of writing. **No
release was created and no version tag was added** — the last tag is still
`v0.0.1`, and `python tools/release.py --dry-run` offers `v0.0.2`.

Read `CLAUDE.md` first, then `AGENTS.md`, then this file. `SUBTITLE-LOG.md`
has the subtitle measurements and the release procedure; `NIGHT-LOG.md` has
the earlier real-device work.

## Before anything else on a new host

* **History was rewritten** in mid-September. A clone made before then will
  not fast-forward: every commit has a new hash. Compare content, not hashes,
  and reset to `origin/development` rather than merging, or every commit
  arrives twice.
* **Hosting is GitHub Pages, not Cloudflare.** The repository is public,
  releases publish to `noflevi.github.io/kodi`, the in-add-on updater reads
  `github.com/NofLevi/kodi/releases/latest/download/addons.xml`, and the test
  channel is the `test-channel` branch. Publishing needs no secrets. Anything
  mentioning `wrangler`, `.cf-token` or `pages.dev` is history.
* **Not in git, copy by hand if wanted:** `subtitles.jsonl` (422 surveyed
  titles, the source of every subtitle number quoted), `.kodi-test/` (portable
  Kodi, `python tools/setup_kodi.py` rebuilds it).

## Current verification state

* **1,828 tests pass** (`python -m pytest tests`, Windows, Python 3.11, about
  75 s).
* Every Python file changed since the 10 September report parses under
  **Python 3.8** grammar (`ast.parse(..., feature_version=(3, 8))`, 22 files) —
  the suite itself ran on 3.11, so this is the check that stands in for Kodi's
  interpreter. CI's 3.8 matrix has not been run for this batch.
* Benchmark (`python tools/bench.py`): total 74 ms, `outlook.annotate` 18 ms,
  `sync.synchronise` 38 ms — no regression.
* No e2e or GitHub workflow was run, per the standing instruction: those run
  before a release, not after commits.

## What this batch changed, and how each was checked

### Fixed — found in a real Kodi 21 on Windows

* **Every OpenSubtitles search failed on a real device** (`3bcb2f5`). The
  stdlib HTTP path flattened response headers into a case-sensitive `dict`;
  OpenSubtitles sends `content-encoding: gzip`, so gzip bytes reached the JSON
  parser and every language logged "did not answer". Machines with `requests`
  never saw it. Responses now keep `email.message.Message`, case-insensitive
  for every header. *Checked:* live on the no-`requests` path, Hikaru no Go
  1x05 now returns subtitles; regression test added.
* **The search box could not be typed into on a keyboard** (`89ff406`). Kodi
  21's `Action` has no character, so keys ran the keymap — one letter opened
  "No PVR add-on enabled", Backspace walked out of Katan. The box is now an
  `edit` control, focused on open. *Checked in Kodi with real keystrokes:*
  "hikarx", Backspace, "u" gave "hikaru" with 5 suggestions; the log shows
  keyboard mode. Return arrives as Select (7), which opens Kodi's modal
  keyboard on an edit control — with a query typed it is closed and the search
  runs. *Not fully confirmed:* the Enter-to-search path, because the test
  harness could not reliably keep Kodi in the foreground.
* **The Gemini key field was hidden at Expert level** (`0773633`); now shown at
  every level under AI translation.

### Subtitles and AI translation — logic tested, not yet watched in Kodi

* **AI-native source selection** (`aa7b3fa`, `34dd167`). With a translation
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

### A title's own language — `bb8f4ef`, `403fdf6`

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

## Known gaps and next work

Status against the 10 September list, then what this batch added.

1. ~~Recreate and track `AGENTS.md`~~ — **done**, tracked.
2. ~~Kodi modal/auto-return invocation loop~~ — **fixed** in `366fa40`; not
   re-observed on Kodi 21.
3. Full unfiltered suite after pull — **done here**, 1,828 passing. Ruff,
   compileall, package build and ZIP-member checks were not re-run.
4. Run under **Kodi 21** — partly: this host is Kodi 21.3 portable on Windows.
5. **Clean `Application.Quit` — reproduced as broken.** On 17 September Quit
   left Kodi running: `CPythonInvoker(... main.py): script didn't stop in 5
   seconds - let's kill it`, the process still alive 40 s later, and no
   `[Katan] service stopped` line. The Katan window's plugin invocation does
   not honour Kodi's abort. This is the highest-priority runtime bug left.
6. UI worker lifecycle in Search/Home/Auth — still open.
7. Update authenticity (signed manifest, pinned key) — still open.
8. Pastebox is plain LAN HTTP — still open.
9. Entitlement values staying volatile in a real flow — still open.
10. **Physical ARM validation (U4 projector, Mi Box) — still not done**, and
    it is what this add-on exists for. Everything above is proven on Windows
    at best.
11. ~~Next source after a decoder failure~~ (`f1821c2`), ~~local resume~~
    (`6a173b9`), ~~watchlist removal~~ (`366fa40`), ~~subtitle runtime
    completeness~~ (`c7430e8`) — **done**. Kids PIN lifecycle and device
    capability filtering remain.

New from this batch:

12. **Watch an AI translation end to end** with a real Gemini key, on a
    playback with no fitting Hebrew subtitle.
13. **See the four original-language features in Kodi**: the dramas row, the
    "Hebrew audio" badge on an Israeli title, a dub ranked below its original
    in the picker, and the audio track switching on a dual-audio anime file.
14. **Re-run the subtitle survey.** Its numbers — 29% of titles with sources
    get no subtitle, 48% of anime, 47% of foreign-language — were measured
    before the header fix, which alone may change them substantially, and
    before absolute anime numbering and the AI-native sources.
15. **Subtitle coverage**: Podnapisi and SubDL (anonymous, strong on European
    languages) are still not providers; 11% of downloaded subtitles failed to
    parse and `failed_bytes` now records why on the next survey.
16. **`fit_segments` still has a kill criterion**: re-survey the seven
    badly-timed subtitles in `SUBTITLE-LOG.md`; if it repairs fewer than about
    two, delete it.
17. **Cut v0.0.2** once 5, 12 and 13 have been looked at, then install it on
    the projector — the only place 10 can be closed.

## Safety and release state

* No credentials, signed URLs, cookies, entitlement tickets or private request
  data belong in this report or any tracked file. The repository is public.
* A future `AGENTS.md` must stay source-only and outside generated ZIPs and
  `repo/`.
* Pushing `development` does not publish. Do not create or push a version tag
  until the owner explicitly asks for a release.
