# Night log — 7 September 2026

Read this first. Newest entries at the bottom. Anything you should look at
personally is marked **NEEDS YOU**.

---

## NEEDS YOU (as of the last entry)

1. **Trakt client id and secret.** Not provided. Watchlist, continue-watching
   and scrobbling stay unverified. trakt.tv/oauth/applications, redirect URI
   `urn:ietf:wg:oauth:2.0:oob`.
2. **The repository is private**, so Kodi's auto-update 404s. Your call, no
   code change needed.
3. **AniList is down at the source** — 403 to everybody, "temporarily disabled
   due to severe stability issues". The anime row is hidden until it comes
   back. Nothing to do, nothing to fix.

TorBox and TMDB are both connected and working. Films and episodes play.

---

## The night in one screen

If you read nothing else, read this.

**The add-on now does the thing it was built for.** Before tonight not one film
or episode had ever played. The last run played **5 of 5** — three films, two
episodes and a 73-file season pack — in a real Kodi 21 with the player
reporting `speed=1`, and a Hebrew subtitle was found, downloaded and applied to
one of them automatically.

**Six things were broken in ways no test could have found**, and all six are
fixed and confirmed:

0. **Down never left the first row of the home screen.** The main screen. Six
   presses and the focused control never changed — everything below row one was
   drawn, looked right in every screenshot taken all night, and could not be
   reached with a remote. Found by asking Kodi which control it thought was
   focused instead of inferring it from pixels.
1. **The source picker crashed for everyone who opened it** — one `.strip()`
   bound to a tuple instead of a string, inside `onInit`, where an exception
   just silently abandons the rest of the method.
2. **The subtitle chooser had never once been reachable.** Kodi runs the plugin
   source for a subtitle module belonging to a video add-on, so `subtitles.py`
   has never executed and `action=search` was landing in the video search
   window.
3. **"Show all" in the picker showed the same eight rows** it was toggling away
   from. It shows 48 now.
4. **Every button in every window was white text on a near-white focus
   texture** — the one control whose label mattered was the one you could not
   read.
5. **DEFAULTS had drifted into being `balanced`**, so an add-on written for a
   projector with a gigabyte of RAM shipped w342 posters and four workers.

**It is lightweight by default now**, which is what you asked for: the shipped
settings *are* the low-memory profile, key for key, with a test saying so.
About **6 MB of visible artwork**, measured on the device report. The visual
polish is one switch that can only ever raise what a profile chose.

**And it is faster.** The plugin's own work between pressing play and handing
Kodi a URL went from 5–8 seconds to **1.4–3.9**, by not downloading the entire
TorBox account (466 KB, 58 torrents) to look up one hash.

Tests went from 559 to **756**. Everything is committed and pushed.

**On the keys, since you asked twice.** Checked properly, not just glanced at:
neither the TorBox key nor the TMDB key appears in any tracked file *or in any
commit in the entire history* — `git log --all -S` on each, zero hits. The only
`settings.xml` in the repository is the add-on's own declaration file, and all
seventeen of its credential settings ship empty. The real keys live only in
`.kodi-test/portable_data/userdata/...`, which `.gitignore` catches twice over,
and a scanner runs before every push.

---

## Confirmation matrix

How each feature was checked, and what the check actually showed. Nothing is
marked confirmed on the strength of a passing unit test alone.

| Feature | How it was checked | Verdict |
|---|---|---|
| Live TV — 46 channels | Every channel resolved and asked for bytes; three played in Kodi at `speed=1` | **Confirmed.** 81 of 84 stream. The three that do not are gone at the broadcaster, not unimplemented. |
| Live TV — 35 radio stations | Same probe | **Confirmed** |
| Keshet 12 and its sister channels | Akamai ticket minted, played in Kodi | **Confirmed.** Thirteen channels that used to be hidden now work. |
| DASH channels without inputstream.adaptive | `kodi.has_adaptive()` in a Kodi that lacks it | **Confirmed hidden** rather than listed and broken |
| VOD — all 7 broadcasters | Each opened and an episode played in Kodi | **Confirmed.** Kan, Keshet, Reshet, Sport 5, Now 14, Sport 1, 891FM. |
| VOD catalogue browsing | Route sweep + screenshots | **Confirmed.** Kan shows 11 Hebrew categories with counts; Keshet 671 programmes. |
| Debrid playback — films and episodes | Five titles through the real route, last run 12:55 | **Confirmed — 5 of 5.** Shawshank, Dark Knight, Inception, Breaking Bad S01E01 and Game of Thrones S01E01, all at `speed=1`, including a 73-file S01–S08 pack from which the right episode was picked. **Two of the five only played because of the fall-through**: their first source was one TorBox had accepted but was still downloading. |
| Time to first picture | Timed on the lean defaults | **5 to 15 seconds**, and nearly all of it is Kodi's own probe of the TorBox URL, not the add-on. The plugin's own work — find, rank, resolve — is now **1.0 to 2.3 seconds**, down from 5 to 8: see 11:30. |
| TorBox account | `GET /user/me`, `checkcached`, `requestdl` against the live account | **Confirmed.** Plan Essential, premium to 2026-10-23. Both undocumented response shapes now measured and tested. |
| Source ranking and the picker | Opened the way a viewer does — home, film, "choose a source" | **Confirmed.** Six ranked rows with release name, provider, size, seeders and "TorBox 1080P במטמון". It was crashing this morning. |
| "Show all" in the picker | The toggle pressed in Kodi | **Confirmed.** 6 rows become **48**, and the status line goes with it. It used to hand back the same six. |
| Reaching the picker's buttons | Same run | **Fixed.** They were on Left only, below a list that swallowed Down. I could not find them twice myself — see 10:20. |
| Home window | Opened in Kodi, and its focus traced through JSON-RPC | **Confirmed, after a serious fix.** Hero backdrop, Hebrew headings, sharp posters - and **down now moves between rows**, which it never did before. See 13:30. |
| Details window — a film | Opened in Kodi | **Confirmed.** Poster, title, meta, plot and cast all in Hebrew; four legible buttons. |
| Details window — a show | Silo, reached the way a viewer does | **Confirmed, after two fixes.** Four seasons with thumbnails and episode counts, then ten episodes with Hebrew titles, air dates and runtimes. Runtimes read "62 min" and a one-episode season read "1 פרקים" — the plural. Both Hebrew now. |
| Search window | Opened in a Hebrew interface | **Confirmed.** Hebrew keyboard by default, recent searches, results. |
| Settings dialog | 134 labels as string ids, integrity test, opened in Kodi | **Confirmed.** Every label renders; every declared setting reads back as declared. |
| Accounts screen | Opened in Kodi | **Confirmed.** TorBox `[OK]` with plan and expiry, TMDB `[OK]`, Trakt `[  ]`. |
| Device report | Run on this machine | **Confirmed.** 34 checks; the two failures are properties of this PC (no hardware HEVC, no inputstream.adaptive), not the add-on. |
| Lightweight default | Device report on a fresh profile | **Confirmed.** w185, 12 a row, **about 6 MB of artwork**, profile `low_memory`. |
| Visual-polish switch | Tests plus the wizard step | **Confirmed.** Raises to w342/20 (~22 MB), never lowers a richer profile. |
| Every route opens | `drive_kodi.py`, 9 paths | **Confirmed.** 0 failed, 0 Python errors. Home 126 ms, search 128 ms. |
| A brand new install | Kodi run against a cleared add-on profile | **Confirmed, after a fix.** Setup, live TV, VOD and tools — 43 channels and 7 broadcasters that need no key. It used to show setup and nothing else. Zero window problems, no credentials written. |
| The setup wizard | Driven end to end from that fresh profile | **Confirmed.** Six Hebrew steps; choosing TMDB shows where to get a key, the keyboard takes it, it is stored correctly, and the home screen fills with twelve rows immediately after. |
| Subtitles — the automatic path | A real playback of a real film | **Confirmed.** A Hebrew subtitle was found, downloaded and applied; the player reports it as a `heb` track. File hash computed in 1.9s. |
| Subtitles — the manual chooser | Opened on that playback | **Confirmed, after a fix.** 33 subtitles, the applied track first at "100% מובנה" with Kodi's SYNC badge, then honest estimates. **It had never once been reachable** — see 09:40. |
| Subtitles — sync and translation | 57 tests against fixtures | **Confirmed by test only.** Translation needs a Gemini key. |
| Kids mode | Exercised in Kodi | **Confirmed, after two fixes.** 17 rows become 8, all four kids rows have content. Two safety failures found and fixed — see 08:50. |
| Trakt | — | **Blocked.** No client id or secret. Watchlist, continue-watching and scrobbling are unverified. |
| Ktuvit | 19 tests against fixtures | **Blocked.** Needs a members' account. |
| MDBList | 19 tests against fixtures | **Blocked.** Needs a key. |
| AI subtitle translation | 30 tests against fixtures | **Blocked.** Needs a Gemini key. |
| Anime rows | AniList probed directly | **Blocked upstream.** The API is refusing everybody; the row is hidden rather than empty. |
| Auto-update | — | **Blocked.** The repository is private. |
| Audio | — | **Not tested, by your instruction.** Every playback check was visual and by `speed=1`. |

---

## 00:50 — TorBox connected, and two facts we had been guessing at

The key is stored only in
`.kodi-test/portable_data/userdata/addon_data/plugin.video.katan/settings.xml`,
which git ignores twice over. Verified it appears in no tracked file.

`debrid/torbox.py` had never executed once. It has now:

- **Auth works.** `GET /user/me` returns plan 1, premium until 2026-10-23.
  Not a free account, so it can actually stream.
- **`checkcached` returns a LIST of `{name, size, hash}` objects**, and only
  for hashes it actually has — a miss is an absence, not a `false`. This was
  genuinely unknown; the client handles both shapes and the list-of-dicts
  branch is the one that fires. Had it been wrong, every source would have
  read as uncached and, with `cached_only` on by default, nothing would ever
  have played.
- **43 torrents are already on the account.** `_existing()` re-fetches that
  whole list on every single resolve, which will only get slower. Noted for
  the polish pass.

## 00:55 — Fixed a bug I introduced earlier today

The home window was drawing its two row headings above **completely empty
rows**, with no hero and no artwork. The main screen of the add-on.

I caused it. Adding `prepare()` — the fix for the focus bug — sets
`self.rows` before the window is shown, and `onInit`'s "have I already run"
guard was reading exactly that attribute. So `onInit` returned immediately and
never filled anything.

The suite could not catch it: every home test calls `onInit()` directly and
none of them call `prepare()` first, so none ran the order the add-on actually
uses. There is now a test that does, plus one asserting the second `onInit`
(which Kodi fires when a dialog closes) stays a no-op.

Found by an audit agent reading the code, then reproduced in a real Kodi from
a screenshot taken after the change. Confirmed fixed the same way.

## 01:20 — A film played. This had never happened before.

The whole point of the add-on, and until tonight not one film or episode had
ever played. Through the real route — `action=movie`, the URL the details
screen builds — not a harness:

| Title | Sources found | Result |
|---|---|---|
| The Shawshank Redemption | 166 → 8 ranked | plays, 2h22m |
| The Dark Knight | 120 → 8 | plays, 2h32m |
| Inception | 147 → 8 | plays, 2h28m |
| Breaking Bad S01E01 | 131 → 8 | plays, 58m |
| Game of Thrones S01E01 | 196 → 8 | plays, 1h01m |

**5 of 5.** Torrentio returns the sources, TorBox reports which are cached,
`scoring` ranks them, `pick_file` picks the file and TorBox hands back a CDN
link Kodi plays. The subtitle pipeline runs on top of it — the log shows a
file hash computed in about a second and a Hebrew subtitle search starting.

The season pack case is the one that could have played the wrong thing, and it
did not. Game of Thrones resolved to a **73-file S01–S08 pack**, and
`pick_file` picked `S01E01.Winter.Is.Coming…mkv` and rejected all 72 others.

**A correction I owe you:** I first reported GoT as playing a 1m37s file and
called it a wrong-file bug. It was not. My own test script printed the
`minutes` field of the duration and dropped `hours`, so `1h01m37s` printed as
`1m37s`. The add-on was right and my instrument was wrong. Re-measured over
75 seconds: 61 minutes, exactly right.

Two things that were genuinely unknown about TorBox are now known and will be
written into tests rather than left as guesses:

- `checkcached` answers with a **list of `{name, size, hash}` objects**, and
  omits misses rather than returning false for them.
- `requestdl` answers with a **bare URL string**, not an object.

## 01:55 — DEFAULTS was being ignored for every boolean setting

Writing the first tests for `play.py` turned up something worth more than the
tests. `settings.get_bool("sources.autoplay")` returned **False**, though the
setting ships as `"true"`.

`get_bool` handed `get()` an empty string as its fallback, and `get()` returns
any fallback that is not None *instead of* consulting `DEFAULTS`. So an unset
boolean came back as `""`, and therefore False, whatever the table said. That
covered seventeen settings that ship as true, including `sources.autoplay`,
`sources.cached_only`, `sources.prefer_hebrew` and `sources.source_memory`.

Real Kodi mostly hides this, which is why it survived: Kodi writes every
declared setting into its own settings.xml from the `<default>` in the XML, so
the value is rarely genuinely absent. It bit exactly where a setting was *not*
declared — and until earlier tonight twenty-three settings were not.

Fixing it broke ten existing tests, which is the interesting part. They were
passing only because `cached_only` was wrongly false, so nothing was ever
filtered; they had been ranking sources that production would have discarded
before ranking began. They now state their precondition instead of inheriting
a bug, and there is a test asserting **every** declared boolean reads back as
declared, so a new one cannot slip through.

Also in this batch, both from the code audit:

- `play._resolve` had its own copy of `registry.resolver_for`, and the copy had
  drifted: it never checked the named service was still configured, so a source
  cached by an account you had since removed went to a client with no key. It
  now calls the real one.
- `registry.forget_cache_status()` existed and was never called, while a
  negative cache answer is remembered for an hour — so a torrent that became
  cached still read as uncached. Tools → Clear cache now clears it, which is
  the one moment you have asked for exactly that.

`torbox.py`, `play.py` and the settings defaults now have tests where they had
none. 602 tests, all green.

## 02:40 — The source picker was broken for everyone who opened it

With TMDB connected the home screen finally has real rows, and the details
window and source picker could be seen with something in them for the first
time. The home screen looks the part: hero with a backdrop, Hebrew row
headings, real artwork.

The picker did not. It showed a title, no status, a blank button and an empty
list. The traceback names the line:

    bits.append("%s %s" % (kodi.localize(32330), service.upper()).strip())

`.strip()` binds to the **tuple**, not to the formatted string, so `_badge`
raised on every cached source. Cached sources rank first, so the first row
took out the entire list, inside `onInit`, where an exception is not a stack
trace anyone sees — it just silently abandons the rest of the method. That is
why the title was set (it happens on the line before) and nothing after it was.

It survived because `sources.autoplay` ships on, so the picker only opens if
you deliberately ask to choose a source.

Fixed, along with four things from the audit: the window now sets its
properties before it is shown (the toggle button's entire label is a property,
so it painted blank), `_render` is wrapped so one bad row cannot abandon the
rest, the debrid service is named the way it names itself — `TorBox`, not
`TORBOX` — and an unknown quality is no longer labelled `SD`, which was a claim
rather than a fallback. The window has tests now; it was the only one without
any.

**A second correction.** Between fixes I reported the picker rendering only
one row, then none. Neither was true. Kodi renders lazily when idle — those
screenshots show FPS 2 and 4 — so the capture returned a stale frame and a
keypress forced the redraw. All eight rows were always there. I have stopped
trusting a screenshot taken while nothing is moving.

615 tests.

## 03:20 — Play did not work on a show until you had already done its job

The details window opens on a show showing its **seasons**, with Play as the
control that has focus. Pressing it called `_next_unwatched`, which looked
through what was on screen for an episode, found only seasons, and gave up
with "no episode found". You had to open a season first — which is the work
the Play button exists to save you.

It now walks the seasons in order and takes the first unwatched episode, with
season zero sorted last because "play the next episode" never means the
specials. Three tests, including one where season one is finished and it has
to move on to season two.

Two smaller things in the same window:

- The backdrop fell back to the **poster** when a title had no fanart, so a
  portrait image was stretched across 1920x1080. The home window reached the
  opposite conclusion and wrote it down: a plain background looks better than
  a stretched poster. Now they agree.
- The information line read `110 min` — the one word in it that was never
  translated, in a window that is otherwise entirely Hebrew.

Seen with real data for the first time tonight, and the film view looks right:
poster, title, year, rating, runtime, genres, plot, cast, and four Hebrew
buttons.

618 tests.

## 04:10 — "Show all" showed the same eight, and a cache flag could not come down

Two bugs in the aggregator, both in the ordering rather than in any of the
well-tested pieces underneath it. `aggregator.py` is 176 lines of
orchestration and had **no tests at all**; it has 22 now.

`find()` cached the eight sources it returned, and `all_sources()` read back
that same cache entry and re-ranked it — so the "show all" toggle in the
picker handed you the same eight rows it was toggling away from. The full
post-filter list existed only inside `find()`'s stack frame. The whole ranked
list is cached now and the short list is a slice of it, so the two views
cannot disagree about what was found.

The second one had a docstring describing exactly what it did not do.
`_recheck_cached` exists because "playing a source that is no longer cached is
the most annoying possible failure" — and it asked only about the sources
already marked *un*cached, so a flag could go up and never come down. It asks
about all of them now, and re-ranks when an answer moves, because being cached
outweighs every other signal and with `cached_only` on it decides whether a
source is shown at all. A source the service has evicted disappears from the
picker instead of sitting at the top of it. A lookup that *fails* is still not
a "no": the flags are left alone, or a network hiccup would empty the picker.

## 04:55 — Say it in Hebrew, survive a payload that changed shape, never hang

Six things, all about what you see when something goes wrong.

The three toasts in the router were the last English text on a path you can
reach, and one read `Unknown action: vod_category` — an internal identifier
shown to somebody who can do nothing with it. The performance-profile chooser
was half translated: Hebrew profile names, English reasons, so it said
"low memory <- only 180 MB free". Seven new strings.

**The device report stays in English on purpose**, and now says why in its own
docstring. It exists to be written to the log and pasted to whoever can help,
and half of it is untranslatable anyway — HEVC, SQLite, System.HasHWDecoder. A
Hebrew log line is a log line nobody can search for.

**The subtitle dialog could hang.** Closing the directory is the only thing
that ends Kodi's "searching for subtitles" spinner, and each branch closed for
itself — so a provider that raised skipped the close and left you with no way
out but to dismiss the dialog by hand. The close lives in one `finally` now.

The Stremio adapter is how three of the four providers speak. One stream it
could not read aborted the loop, so a cosmetic upstream change would read as
"no sources found". A bad stream costs itself now. `videoSize` has arrived as
a string and as a float and `int()` accepts neither; a `fileIdx` that is not a
number is ignored rather than allowed to pick a file out of a season pack.

Two more of the same shape: a channel whose `index` is not a number sorts last
instead of raising out of the sort, and a catalogue entry whose poster is JSON
null lists without one — `get("i", "")` does not help, because a null is a
present key and the default never fires.

Deleted `sources_window.quick_pick` and `diagnostics.summary`, neither called.

## 05:30 — Two settings that promised something with no code behind them

`allow_uncached` was read by three debrid clients and written by nothing, so
it was always false. That made **"cached only: off" a trap** rather than a
choice: the picker listed uncached sources, the client refused to open them,
and pressing play did nothing at all — no download, no message, nothing.
Turning that setting off is you saying you will wait for a download, and it
now means that. Left on, which is the default, nothing changes and TorBox's
sixty-an-hour uncached quota is untouched.

The VOD category link dropped the broadcaster, so "Kan 11 (216)" could list
every broadcaster's programmes under that name. **I should be exact: nobody
has seen this.** In today's catalogue no category name is used by two
broadcasters, so the two answers coincide. That is an accident of the data,
which tooling regenerates, not something the code arranged, and the test says
so in as many words.

## 06:20 — Lightweight is now what it ships as

Your steer was "make the default the lightweight, and a switch for visual
polish". Here is what that meant in practice, because it was worse than it
looked.

The add-on **shipped `balanced`**: w342 posters, twenty a row, four workers, a
50 MB cache, prefetching on. `DEFAULTS` had quietly become a fourth profile
that nobody maintained and that disagreed with all three of the ones written
down. On a projector with a gigabyte shared with Android, three visible rows
of w342 posters is about 22 MB of decoded bitmaps against 6 MB at w185.

`DEFAULTS` is now `LOW_MEMORY`, key for key, **and a test says so** — the
shipped state and the lean profile can no longer drift apart. Twelve values
moved.

The polish is one switch rather than a profile name: `ui.rich_visuals`, off,
in the interface settings and as a fifth step in the setup wizard. It is a
floor, not an assignment — it lifts artwork to at least w342 and twenty a row
and cannot lower a profile that already asks for more, so turning it on with
`powerful` selected still leaves you at w500. Flipping it re-applies the
current profile, because the poster width lives in the profile tables and the
setting alone would have changed nothing until the next time a profile was
applied, which for most people is never.

Both choices are shown **with what they cost in megabytes**, computed rather
than written down. The wizard also shows what the device says about itself —
`recommend()` has always read free memory, cores and panel resolution and
nothing ever called it — but a box with room to spare is *told* so, not
quietly switched. That is deliberate: it is memory being spent on somebody
else's hardware.

**Worth knowing:** an install that already exists keeps the values it has, so
this changes what a *fresh* install gets. Tools → Performance profile applies
the lean set to an existing one.

*(Corrected at 12:20. I wrote here that Kodi writes every declared setting into
its own file the first time it loads an add-on. It does not: a fresh install
has no settings file at all until something is changed, and `DEFAULTS` is what
the add-on runs on until then. Which is better for this change, not worse —
a new install gets the lean settings without Kodi having to write anything.)*

Photographed at w185 in a real Kodi. It does not look cheap: sharp posters,
the hero backdrop, Hebrew headings, two full rows.

## 06:50 — The button you were about to press was the one you could not read

Every button in all four windows had white text, and the focus texture is
near-white. So the focused control — the only one whose label matters at that
moment — was white on white. Forty-four buttons now declare a dark focused
colour. The search window's on-screen keyboard was the worst case: thirty-five
keys, and the one under the cursor invisible.

"הוסף לרשימת Trakt" was clipped at both ends in the details window; four
Hebrew words and one Latin one do not fit in 270 px. And a focused poster's
title now scrolls instead of truncating, because "Obsession (2..." tells you
less than the poster above it already did.

**A third correction.** Between those two fixes I read a screenshot as showing
the details window drawing no poster at all. It draws it fine — the frame was
captured before the image finished loading, and Kodi renders lazily when idle
so the same stale frame came back twice. That is the third time tonight the
same trap has caught me. Nothing was wrong and nothing was changed for it. I
now press a key before every single capture.

Confirmed in the same pass: the runtime line reads "110 דק'" rather than
"110 min" — the localisation fix from 03:20, seen working in Kodi rather than
in a test.

683 tests.

## 07:30 — One route led nowhere, and AniList is the reason

The route sweep reported one path returning nothing: the anime row. It is not
a bug at this end. **AniList answers every request with 403** —

> "The AniList API has been temporarily disabled due to severe stability
> issues."

— to any User-Agent, a browser one included. An outage at the service. It will
fix itself and nothing here needs changing when it does; the module says so at
the top so the next person to look does not spend the twenty minutes I did.

What did need changing is what you see. The custom home window already hides a
row that came back empty. The plain listing was still offering one, so the
anime row — and a Trakt chart with nobody signed in — was a menu entry that
opens an empty screen. A row that has *never been warmed* is not the same
thing and is still offered, or a fresh install would show nothing at all.

Also from the same sweep: the device report and the setup wizard each had
their own arithmetic for "what does this artwork cost". They agreed today, by
luck. One implementation now.

And I wrote, **for the second time in one night**, the `.strip()` that binds to
the argument tuple instead of the formatted string. The first one took the
entire source picker down. There is now a test that scans the source for the
pattern; it found this one immediately, and then found my own comment
describing it.

## 08:05 — Two things a test would never have shown

The search window opened on the **Latin** keyboard. The whole add-on is
Hebrew, and the live channels and the entire on-demand catalogue are titled in
Hebrew, so every one of those searches began with a trip to the charset
button. It opens on the matching keyboard now — the Hebrew grid, focused on א,
with 123 offered as the next set.

The accounts screen marked Trakt and TMDB with `[OK]` or `[  ]` and marked the
debrid services with **nothing at all** — on the screen whose entire purpose is
to say which accounts are working. A service whose token had expired looked
much like one that was fine.

700 tests. Zip 358 KB against a 600 KB budget.

## 08:50 — Kids mode, and two ways a safe list stops being safe

25 tests, never run in Kodi. It works: seventeen rows become eight, only the
structural entries survive, and the rows are real Hebrew children's content.
But the Israeli row was empty, and filling it turned up something worse than
an empty row.

Empty because of arithmetic nobody had done: of eighty-four channels exactly
one is a children's channel, and it is a DASH stream, so without
inputstream.adaptive there was nothing left. The word list was English against
a Hebrew channel list, matching on the key alone.

So the row now draws on the broadcasters' own children's sections — Kan alone
publishes one with twenty programmes. And then:

* **"הופ", for the Hop! channel, is three letters, and Hebrew has no word
  boundary there.** It matched "הופעה" (performance) and "הופקר" (abandoned).
  That is how a documentary about 7 October ended up in a row for small
  children.
* **A programme's name describes its subject, not its audience.** Matching
  names also pulled in "לא לפני הילדים", "מחפשת תשובה - חינוך ילדים" and
  "הילדים האבודים" — three adult programmes *about* children.

Only the broadcaster's own section says who something is *for*, so that is the
only signal used for on-demand programmes now. Channel names keep matching,
because a channel's name does name its audience.

Separately: HTML entities were reaching the screen. "ירדן ודידי בפיג&#x27;מה"
is a real programme and that is exactly what the row said. Unescaping happens
on load, the one path everything comes through, because search matches on the
stored name and would otherwise be matching text nobody can type.

## 09:05 — A regression I nearly reported, and one I did not

The playback test came back **0 of 5**. It had been 5 of 5.

It was my own test profile: `sources.autoplay` was left `false` from the picker
work at 02:40, so the add-on was correctly opening the source picker and
waiting for a keypress nobody sent. Nothing was broken. With autoplay restored,
4 of 5 played at `speed=1`.

The fifth, Game of Thrones, resolved to a URL that TorBox's CDN then would not
open. Its own end, not ours.

And a measurement worth having: **Kodi spends ten to thirty seconds on its own
probe of the resolved URL before it opens a player.** The add-on's work — find,
rank, resolve — is two to eight seconds of that. A fixed twenty-second wait in
my harness was reporting working films as "not playing", which is the third
instrument error of the night and the reason the harness now polls.

## 09:40 — The subtitle chooser had never once been reachable

The one screen never seen. Opening it on a real playback showed "No subtitles
found", and the reason is not a subtitle bug at all.

**Kodi runs the plugin source for a subtitle module belonging to an add-on that
is also a video plugin.** The dialog calls

    plugin://plugin.video.katan/?action=search&languages=English

and Kodi resolves that to `main.py` — never to `subtitles.py`. So the declared
`xbmc.subtitle.module` library has not executed once in this add-on's history,
and `action=search` landed in the **video search-window route**, which tried to
open a window over a modal dialog and failed.

Nothing in the unit suite could have found it: every subtitle test calls
`subs.service.dispatch` directly, which is precisely the function Kodi was
never reaching. It took opening the dialog on a real playback and reading which
`.py` Kodi actually ran.

The router recognises a subtitle request now. `manualsearch` and `download` are
unambiguous, and a test asserts no route of ours ever takes those names.
`search` is the collision, and Kodi always sends `preferredlanguage` with it,
which none of our own URLs do.

Second thing, found on the way: Kodi ships with its subtitle language set to
**English** and passes that here. Wizdom is a Hebrew site, so even with the
route fixed a Hebrew viewer on a stock Kodi would have been told "no subtitles
found" — true of the question asked, useless as an answer. The search is now
the union of what Kodi asked for and what this add-on is configured for, in
Kodi's order: an explicit request is honoured and never dropped.

**Seen working**: 33 subtitles for The Shawshank Redemption, the applied Hebrew
track first at "100% מובנה" with five stars and Kodi's SYNC badge, then the
downloadable candidates as honest estimates — 33%, 27%, 15% — each naming its
release and why it scored what it did. The automatic path had already found,
downloaded and applied a Hebrew subtitle before the dialog was even opened.

715 tests.

## 10:05 — SubSource cannot answer any more

I went to check whether the lean profile was wrong to switch SubSource off.
Disabling a Hebrew subtitle source to save one HTTP request looked like a bad
trade in an add-on whose point is Hebrew subtitles. The profile was not the
problem.

Measured today: the whole `POST /api/...` surface this module was written
against answers 404, and the `/v1` REST API that replaced it answers **401
"Not authenticated"**. There is no anonymous search route left. Enabled or
disabled, SubSource returns nothing.

So it is off in **every** profile now, not only the lean one, and switching it
on says what happened in the log rather than contributing nothing in silence.
That leaves **Wizdom as the one working Hebrew source** — OpenSubtitles needs a
key, Ktuvit needs an account. Wizdom answered with 32 Hebrew candidates in
0.4 seconds, so the chooser is not short of material, but it is one provider
and worth your knowing.

## 10:20 — I could not find the picker's own buttons

I set out to confirm the "show all" fix in Kodi rather than leave it resting on
tests. It took four attempts, and the reason is the more interesting result.

**The picker's two buttons were reachable on Left only.** They are drawn below
the list, and the obvious way to reach something below a list is to press down
— which the list swallowed, because its `ondown` pointed back at itself. I
failed to find them twice while testing this window. Down reaches them now.

With that: the toggle takes the picker from **6 rows to 48**, and the status
line goes with it. Before the aggregator fix it would have re-ranked the same
six and handed them back.

Three smaller things from those runs. The picker had the home window's
`defaultcontrol` focus bug and now names a button instead of the list. The
toggle's label is longer coming back than going out — "show only the best"
against "show all sources" — and was clipped. And `onClick` logs which control
was pressed, because a picker that closed tells you nothing from a screenshot
about which of its three exits was taken; that line is what stopped me writing
down "the toggle is broken" when what had happened was that I pressed the
other button.

716 tests.

## 10:45 — The plain home screen listed things twice, and things that were not there

Two defects found by reading what Kodi actually returns for the home path
rather than by any test.

**"שידורים חיים" and "VOD ישראלי" each appeared twice**, one directly above the
other. They are catalogue rows *and* sections, under the same two string ids,
and the listing offered both. In the custom window they are horizontal rows of
artwork and the sections are not drawn at all, so the duplication only ever
showed on the plain screen. The folder is the better of the two there.

**Rows that lead to an empty screen are gone too**, which needed a smaller fix
underneath. `catalog.load` never cached an empty answer, so `peek` could not
tell "never warmed" from "warmed and came back empty" — and that distinction is
exactly what the listing needs. An empty answer is now remembered for ten
minutes: short enough that a service which comes back is not hidden for a day,
and never longer than the row's own TTL. A test caught the second half of that:
continue-watching refreshes every five minutes, so remembering "nothing here"
for ten would have made it slower to notice an empty row than a full one.

Seventeen entries become twelve. Gone are the anime row, which AniList cannot
answer, and the two Trakt charts, which have nobody signed in. **Every
remaining entry opens something.**

## 11:00 — And an empty home screen you can leave

Every row hidden means row zero is not there to take focus either, so the
custom window painted black with no way off it but the back button — no search,
no tools, no settings, and nothing on screen saying why.

That state has always been possible. It became easier to reach with the change
above, which is the right trade but makes the dead end worth closing. Focus
falls back to the top bar, and the hero says "אין מה להציג כרגע".

Not "set up Katan", which is what I wrote first and had to correct: by the time
that line runs, `_require_setup` has already returned True, so there *is* a
TMDB key and setup is not the problem. It would have sent you to a wizard with
nothing to fix.

721 tests. The zip is 366 KB against a 600 KB budget.

## 11:30 — Downloading the whole TorBox account to play one film

A note I left at 00:50 said `_existing()` "re-fetches that whole list on every
single resolve, which will only get slower". Measured properly today, against
the real account:

    _existing   2.0 - 3.5 s, 466 KB of JSON, 58 torrents
    _create     0.46 s, and it answers "Found Cached Torrent. Using Cached
                Torrent." with the same torrent_id when the torrent is already
                there

So adding a torrent you already have is not a mistake TorBox charges you for;
it is the cheap way to ask "do I have this, and if not, take it". That is the
first call now, with the account list kept as the fallback for a torrent that
finished downloading but is no longer cached. The order flips when uncached
adds are allowed, because then adding is not free — it spends one of sixty an
hour — so it looks before leaping.

**Resolve went from 5.8 seconds to 1.1**, and it no longer gets slower every
time you play something. In Kodi the whole plugin call is now **1.0 to 2.3
seconds** against 5 to 8.

Two things that turned up on the way, neither caused by the change:

* An indexer can flag a source cached and TorBox will accept the magnet and
  still not have it — state "downloading", cached false, no file list. The
  message for that was "TorBox torrent has no usable video file", which says
  the wrong thing about why. It now says what actually happened, and does not
  spend three seconds waiting for a file list that is not coming.
* **When a source will not resolve, playback falls through to the next one.**
  It used to say "could not play" while the second source in the list would
  have played immediately, which is exactly what happened to The Dark Knight
  earlier tonight. Three attempts, and never when you chose the source
  yourself — you asked for that release.

One honest note on the numbers above. Films still occasionally fail to start,
and when they do the log now shows the add-on handing Kodi a URL in about a
second and **Kodi then spending twenty-one seconds failing to open it**. That is
TorBox's CDN — one edge host, `store-033`, was slow or refusing all night.
Nothing in the add-on can fix it, and the runs where it behaves play 4 or 5 of
5.

732 tests.

## 12:40 — Twelve more functions nothing called, and two of them mattered

Three defects tonight had the same shape: a public function defined, never
called, and in two cases a check somebody meant to make. So I went looking for
the rest. There were twelve, and `test_addon_integrity` now fails if one comes
back.

Writing that test correctly mattered more than running it. My first sweep said
"zero" because it counted the source tree twice and hid everything. It has to
skip route handlers, which are called through the decorator's registry, Kodi's
own callbacks, and methods — `urlsession.redirect_request` is urllib's own API
and looks identical to dead code from outside.

Two were worth wiring rather than deleting.

**"Verbose logging" did nothing at all.** Everything this add-on logs is DEBUG,
and Kodi throws DEBUG away unless the whole application has debug logging on —
a firehose nobody wants to read to find out why one film would not play. The
toggle now writes this add-on's own messages at INFO, so they survive in an
ordinary log. Off by default; nothing changes.

**A subtitle listed as Hebrew and written in English** was applied silently.
`srt.looks_hebrew` existed for exactly that and was never called. The automatic
path checks now and falls through. The manual chooser deliberately does not:
you picked that entry, and second-guessing you would be worse than honouring a
choice you can see and change.

## 12:55 — 5 of 5, and two of them only because of the fall-through

The best playback run of the night, and the log reads like a summary of it:

    sources: 147 found, 60 kept, showing 6 (dropped: 30 HDR is switched off...)
    TorBox is still fetching 66672b822002 (state 'downloading'), so there is
      nothing to play yet
    falling through to the next source: Inception.2010.1080p...GalaxyRG265.mkv
    action movie took 3929 ms

Three separate fixes from tonight, working together in four lines. Inception
and The Dark Knight both had a first source that Torrentio called cached and
TorBox was still downloading — the exact failure that made them "did not play"
six hours ago. They play now.

Every plugin call between pressing play and Kodi having a URL: **1.4 to 3.9
seconds**. First picture: 5 to 10.

744 tests.

## 13:30 — Down never left the first row of the home screen

The main screen of the add-on, and the worst defect of the night.

Six presses of down, and the focused control never changed. I stopped inferring
it from screenshots and asked Kodi directly, which answered "Mayday (2026)"
seven times in a row. Everything below the first row was drawn, looked correct
in every screenshot taken all night, and **could not be reached with a remote**.

The row lists wire left and right — so a horizontal list wraps within itself —
and say nothing about up and down. Kodi's geometric fallback does not find its
way out of the nested groups, so up and down went nowhere.

Fixed in Python rather than in the skin, because only Python knows which rows
came back empty. Those have had their heading cleared and are hidden, and an
explicit `<ondown>` pointing at a hidden row would simply fail. Down moves to
the next row that has something in it, up moves back, and up from the first row
reaches the top bar.

Confirmed the way it was found: down now walks Mayday, Silo, במרוץ נגד הזמן,
ספיידרמן, הלביאות, אנטארקטיקה — seven rows — and up walks back.

**The lesson, again.** Every screenshot of this screen all night looked right,
because it *was* right; what was broken was something a picture cannot show. It
took asking Kodi a question rather than looking at it.

753 tests.

## 13:55 — A show's details, reached for the first time

Fixing the row navigation made this reachable, and it is worth seeing: Silo,
with poster, Hebrew plot, Hebrew cast, four seasons with their own thumbnails
and episode counts, and then ten episodes with Hebrew titles, air dates and
runtimes.

Two things were wrong in it, both small and both Hebrew.

The episode runtimes read **"62 min"**. That is the same defect I fixed in the
information line at 03:20 and did not notice one function below it, in a window
that is otherwise entirely Hebrew.

And season four of Silo has a single episode, which read **"1 פרקים"** - the
plural. Hebrew takes the singular after one.

Both confirmed fixed in Kodi: the list now reads "62 דק'", "52 דק'", "66 דק'".

756 tests.

## 04:10 - The channels were never on the screen at all

"Also I dont see the channels." They were not there, and the reason is worth
writing down because nothing in the project would ever have caught it.

`catalog.enabled_rows()` returned thirteen rows. The home window has ten row
controls, and both `prepare()` and `onInit` sliced the list to fit:

    self.rows = catalog.enabled_rows()[:ROW_SLOTS]

`israel_live` and `israel_vod` were rows twelve and thirteen. So the two rows
this add-on exists for were enabled in the settings, warmed by the background
service, sitting in the cache, listed correctly by every tool that asks the
catalog - and cut off the end of the list before anything was drawn. Every
screenshot of the home screen all night was of a screen that was missing them,
and looked completely fine.

With every account configured it is fifteen rows on by default, so five were
being dropped, not three.

Three things changed.

**The row order.** The Israeli rows are now third and fourth rather than
twelfth and thirteenth. They need no API key of any kind, which makes them the
rows most likely to have something in them on a fresh install, and this is an
Israeli add-on. The two Trakt charts moved down, because they need a client id
nobody has yet and were spending two of the ten slots on nothing.

**Sixteen row slots instead of ten**, so the whole default set fits. The six
new groups are hidden until a heading is set, exactly like the others, and a
hidden group costs nothing to draw.

**The slice says so now.** `_pick_rows()` logs a warning naming every row it
could not draw. The silence was the actual defect; ten was only a number.

Confirmed in Kodi by asking the window what it was showing rather than looking
at it - `Window(13000).Property(katan.rowN.title)` for all sixteen slots:

    0  סרטים חמים        3  VOD ישראלי         6  פופולרי השבוע
    1  סדרות חמות        4  סרטים ישראליים     7  סדרות במגמה השבוע
    2  שידורים חיים      5  סדרות ישראליות     8  עכשיו בקולנוע
                                               9  פרקים חדשים היום

Row two is the live channels. "rows dropped for want of a slot: none."

## 04:30 - Rows that never end, and the cursor that would not stay put

"the series and everything just be infinite [or at least load while scrolling
not all immideatly]". The catalog half was straightforward: every loader takes
a page, `load()` takes a page, cache keys carry it, and rows that are whole
lists rather than pages - the live channels, the VOD catalogue, a Trakt
watchlist - are marked `paged=False` and never asked for a second one.

The window half took three attempts and a measurement each time.

**First attempt.** Fetch the next page on a worker thread when the selection
comes within a few items of the end, and append it. In Kodi this produced
exactly the complaint that arrived while I was testing it: *"When you scrolls
in the movies right faster then it can load it jump back to the first instead
of waiting."* The log, with the cursor position printed either side of the
append:

    row trending_movies grew to 70 items (page 6), cursor 56 -> 0

and quieter versions of the same thing - 30 to 9, 22 to 5 - on nearly every
other page. Adding items to a list Kodi is currently navigating makes it
reflow underneath the cursor.

**Second attempt.** Split it: the worker only fetches, and the items are added
inside a Kodi callback, on the GUI thread, with the position read before and
restored after. Measured again. Still jumping: 22 to 7, 33 to 8.

**What was actually wrong**, and it is the sort of thing only a real Kodi
tells you. `ControlList.addItems` and `ControlList.selectItem` do not act on
the control when you call them - they post *thread messages*. So
`getSelectedPosition()` immediately after an append reads the state from
before it, which meant the restore was guarded on a condition that was never
true and never ran at all. The second attempt looked right, passed its tests
against a synchronous stub, and did nothing.

Removing the guard fixes it, because the two messages are processed in the
order they were posted: Kodi's rebind lands first and the restore lands on top
of it. Forty-five presses of right, traced out of the add-on itself:

    0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26
    29 30 31 32 33 34 35 36 37 38 40 41 42 43 44 45     backward jumps: 0

through four page loads, 12 items to 57.

**And one thing the fix uncovered.** The first live run grew a row to 94 items
during the eighteen-second start-up wait, with no input of any kind. Kodi
sends `ACTION_MOUSE_MOVE` while the pointer merely rests over the window, and
moves the selection on hover, so a pointer left near the end of a row paged
through the catalogue on its own. That is the opposite of "not all
immediately". Hovering is not scrolling, and no longer triggers a fetch. The
log for a full start-up is now empty of page fetches.

The plain directory listing pages too, but it cannot do this - a Kodi
directory is a fixed list with no scroll event to hang a fetch off - so it
ends with a "next page" entry, which is what Kodi's own skins expect.

Ceilings, because "infinite" on a device with a gigabyte of RAM is a promise
that ends in a killed process: 200 items a row, one page in flight at a time,
a row that returns nothing or returns what it already has is marked finished
and never asked again.

## 04:45 - Back, confirmed in Kodi rather than in stubs

Six presses of Back on the home screen: window 13000 every time, "back on the
home screen, staying in Katan" six times in the log. Then into a title's
details (window 13001) and Back once: returns to 13000. So the screen you
cannot leave is only the home screen, and everything inside Katan still goes
back - which was the design, now measured rather than asserted.

`ui.stay_in_katan` still ships **off**. It is on in the test profile here.
Turning it on by default would take over the Kodi of anyone who installs this
from the repository, and that is their call rather than mine - it is one
switch in Settings -> Interface.

774 tests, zip 376 KB.

## 11:20 - A rail down the left, because "all in one" was the problem

"You know what i want left tool bar that let you choose between
series/movies/live tv instead all in one. and each section should have more",
then "i meant more topics like new,popular, classics, etcc", then "I also dont
understand the 'home' 'בית' button".

So: three tabs, not four. The Home tab was the first thing built and the first
thing to go - a button called Home beside Films, Series and Live TV does not
say what it would show, and the viewer said so within a minute of seeing it.
`HOME` survives inside the catalog as "every row that is switched on", which
is what the plain listing and the background service want and is a different
question from which tab is on screen. The window opens on Films.

Each tab is topics first, then genres:

    Films    trending, new, in cinemas, popular, popular this week,
             top rated, classics, hidden gems, biggest hits, Israeli,
             coming soon, the nineties, the eighties, then eight genres
    Series   trending, new, airing today, popular, popular this week,
             top rated, classics, hidden gems, returning, Israeli, anime,
             then six genres
    Live TV  channels, radio, new episodes, then one row per broadcaster

The vote-count floors are the part that matters and the part that is easy to
leave out. Without them "top rated" is a film four people have seen and scored
ten, and "classics" is an obscure 1974 short rather than anything anybody
would call a classic. A classic needs 800 votes; a hidden gem needs at least
80 and fewer than 900, and the upper bound is what makes it a gem.

The broadcaster rows are built from the bundled catalogue rather than listed,
so adding a broadcaster to the data needs no code change here, and their names
are already Hebrew in the file. They are appended separately from the rest of
the row table and retried until they arrive: folding them into the one-time
build meant a single early failure - the service starting before the profile
did - would remove the whole Live tab for the life of the process, silently
and permanently.

Continue watching is the first row and recommended is the second, wherever
they apply. Both need Trakt and both hide themselves without it, so an install
with no account simply starts at what is trending rather than showing two gaps.

**Two things a real Kodi had to teach, again.** A grouplist will not walk
between entries that are each wrapped in a group: the rail's first draft
wrapped every button with its accent bar, and pressing down did nothing at all
- every section below the first unreachable with a remote, no error anywhere.
The buttons sit directly in the grouplist now and the accent bars are drawn
behind it, at the y each button will occupy. And left out of a row only
reaches the rail from the *first* item, because that is when Kodi fires
onleft; that is what a viewer expects and is left alone, but it looked like a
bug twice before it was written down.

Confirmed by asking Kodi what was on the screen rather than looking at it: all
three tabs, 21 rows on Films, 16 on Series, 10 on Live TV including every one
of the seven broadcasters.

Row slots 16 -> 24. Settings moved out of the top bar to the bottom of the
rail, which is where somebody looks for it.

## 12:15 - Signing in without typing

"we need to support all the possibilities of integration of the debrids and
trakt etc.. as QR, api key, link", and then "all the options but make it
logically and simple".

One screen now, offering whatever the service actually has, in order of how
little work it is: scan a code with your phone, open a link and type a short
code, or type the key here. Before this, four services asked four different
ways for the same thing.

Nothing pretends. A service with no device flow does not get a "scan a code"
that opens a page and then asks you to type the key anyway - `methods` on each
client is what decides, and a test fails if a client offers a typed key with
nowhere to find it.

**The QR encoder is written here**, in the standard library alone. Every
online QR service would be handed the authorisation URL, which is a live
credential for the few minutes it lasts, and `qrcode` needs Pillow, which is
not going on a projector with a gigabyte of RAM. Byte mode, versions one to
ten, and a PNG writer on top of zlib and struct.

It is tested against the specification rather than against itself, because a
QR code that is wrong looks exactly like a QR code and the only symptom is a
phone that will not scan it - not something anybody debugs from a photograph
of a television:

* the block table has to add up to each version's codeword count
* all thirty-two format strings have to match the published list
* the Reed-Solomon coder has to reproduce the worked example in the standard
* and the strongest one: every symbol is taken apart the way a scanner would,
  undoing the mask, the zigzag and the interleaving, and has to come back as
  what went in.

Confirmed in a real Kodi with a live device code from Real-Debrid's API: the
code on screen, the link and the six characters beside it, Hebrew throughout.

Two things that were plainly wrong turned up on the way. The accounts screen
listed only debrid services already signed in and hid the "connect a debrid
service" entry as soon as one was, so somebody with TorBox could not reach
Real-Debrid from that screen at all. And a connected service offered only
"connect again" - the one thing somebody looking at a working account does not
want. Signing out is offered now, and clears every credential the service
names rather than guessing: Real-Debrid holds five, and clearing only the
token leaves an account that reads as disconnected and still holds
credentials.

## 12:40 - The failure that looked exactly like success

An episode would not play. The add-on's log said:

    action episode took 1366 ms

and nothing else, ever. I spent three rounds adding log lines to every exit in
`play()` - no title, no debrid service, no sources, nothing chosen, nothing
that would open - and every run came back with the same single line, which
should have been the clue much earlier: the function was not failing. It was
succeeding.

Kodi's own log had the rest of it. `CCurlFile::Stat ... Timeout was
reached(28)`, twenty-five seconds to nothing, and a browser agreed: the CDN
node accepted a TCP connection on 443 and then never answered. Three of
TorBox's north-west-america nodes were doing it on the same evening; a
european one was fine.

So a resolved link is opened for one byte before it is used, and a link that
will not give up a byte falls through to the next source like any other. Only
a connection failure counts against it - an HTTP status does not, because some
CDNs answer a range request with 403 and the whole file with 200.

Dead nodes are remembered for five minutes, because a debrid service hands out
links round-robin and the same dead node comes back for source after source.
The first run of the fix fell over store-028, store-045 and store-028 again,
paying a full eight second timeout for the same host twice. Measured, with and
then without that memory:

    without   30.7 s   store-028, store-045, store-028, then a working node
    with      22.1 s   the second store-028 skipped instantly

Both episodes play. On a healthy CDN the check costs a few hundred
milliseconds.

**The lesson, and it is a new one.** This add-on's job ends at
`setResolvedUrl`. Everything after that happens inside Kodi and is invisible
from in here, so when something does not play, our log is not the log to read.

910 tests, zip 400 KB.

## Are there more scrapers worth having? One, and it is a good one

The question was whether the source list is as wide as it can be. It was
worth asking, because "four aggregators are enabled" was never true: three of
the four ship off, so **for films and series there was exactly one scraper
answering** - Torrentio - and every source the picker has ever shown came
from it. A single upstream is also a single point of failure, which is what
an evening spent on a Bleach episode that Torrentio has no copy of looked
like from the inside.

So every candidate was tried against the live service rather than read about:

    torrentio      63 streams for a film, 0 for the Bleach episode
    torrentsdb     90 streams for the same film, 50 for a Silo episode
    comet          403 without a config; with one, a single notice stream
    mediafusion    200 OK, zero streams
    zilean         404 on every path, its own healthcheck included
    stremthru      404

**TorrentsDB is added.** It speaks the same Stremio protocol the adapter
already handles, needs no configuration, and every stream carries an
infohash, so the aggregator merges it with Torrentio for free. What it is
worth, measured after the merge rather than by counting its own results:

    The Matrix    torrentio 211, torrentsdb 90  ->  235 unique
                  27 torrents nobody else had
    Silo S01E01   torrentio 171, torrentsdb 50  ->  185 unique
                  14 torrents nobody else had

It is asked anonymously. Torrentio is sent the debrid key so it can say what
is already cached; TorrentsDB is not, because the cache question is answered
in one batch by our own debrid clients anyway and there is no reason to hand
a second stranger an account token for an answer we do not need from it.

**Comet and MediaFusion stay off, and the reason is not laziness.** Both now
require a configuration blob minted by their own web UI. Comet's format has
changed since this add-on was written: a hand-built blob is answered with
`OBSOLETE CONFIGURATION, PLEASE RE-CONFIGURE`, and without one the service
answers 403. They can be switched on by anyone who pastes a config from the
site, and the settings field for it is still there - but shipping them on
would be shipping a provider that contributes nothing, which is the thing
this project keeps saying it does not do.

968 tests, zip 416 KB.

## Subtitles you can ask for, and subtitles where there were none

Two requests, and they turn out to be the same machinery pointed at different
problems.

**"What do we do in films where there is nothing?"** Usually there *was*
something. The automatic path asks the providers for the two languages in the
settings - Hebrew and English - and if neither comes back it reports "no
subtitles found" and stops. That sentence was often untrue: an obscure film
with no Hebrew and no English subtitle frequently has a Spanish or an Arabic
one, and translating out of it is a far better answer than nothing. So when
the normal search comes up empty the search now widens to ten languages -
which costs no extra requests, because it is one more value in the same call
and only OpenSubtitles even reads it - and translates whatever it finds. The
timings come from the source subtitle and are never touched, so the result
fits exactly as well as the file it came from.

**"A flag to enable AI even when there are subtitles and I can see they are
not good."** There is now a row at the bottom of Kodi's own subtitle dialog,
and it is there whether or not the list above it is empty. That is the point:
nothing in this add-on can tell a good Hebrew subtitle from a bad one by
looking at it, so a row that only appeared when the list was empty would miss
the exact case being complained about. It works for any language pair - the
target is whatever Kodi's own subtitle language is set to, so it is not a
Hebrew feature - and it says no percentage and shows a flat three stars,
because its accuracy is the accuracy of whatever it ends up translating and
inventing a figure would be worse than admitting that.

It also appears with no API key configured, and says "needs an AI key - press
to set one up". Hiding it until a key exists shows nothing at all to the one
viewer who most needs it.

### Two things the real Kodi taught, neither of which a test could

The first build put the translation on a thread in the plugin process and
opened the key prompt from inside the subtitle dialog. Both are wrong, and
both looked completely fine from Python.

**Kodi's subtitle window is modal, and a modal silently refuses to let
anything open over it.** The prompt was opened, Kodi declined, nothing
appeared - no error, no log line, just a press that did nothing. It is the
same rule that stopped a context menu starting playback until the busy dialog
was closed, met in a second place. Kodi also does not close the subtitle list
when a plugin hands nothing back, and this row deliberately hands nothing
back - there is no file yet, only a promise to make one - so the list stayed
up over the film it was writing to.

**A plugin invocation is torn down the moment it returns.** `player.py` says
so in its first paragraph; it is why the player monitor lives in the
background service. A translation is minutes of work and the plugin process is
gone in milliseconds, so that thread would have been killed part way through.

Both are now the service's job. The dialog leaves a window property, the
service takes it within a second - taking rather than reading, so one press
cannot start two - closes the subtitle list, waits until it has actually gone,
and then does the work on a thread of its own. Measured in a real Kodi over a
live playback: press to service, 170 ms; the list closes; the key prompt opens
over the still-playing film, in Hebrew.

What no amount of this can prove is the only thing left: that the model
returns usable Hebrew. That needs a Gemini key.

### One stray thing found on the way

The automatic path, whenever it translated a subtitle, notified **"Subtitle
found: Anime"**. String 32302 is the heading of the anime row on the home
screen, and it had been passed as the description of what had just happened.

993 tests, zip 421 KB.

## "52 percent is not good enough" was right, and the subtitle was fine

The complaint was about a number, and the number was wrong. Wizdom is
answering perfectly well - 32 Hebrew subtitles for Shawshank, 2 for a Silo
episode - and the *order* it put them in was correct the whole time. What was
broken was the scale.

The best score reachable from a Hebrew provider was **33**. Source, resolution
and codec all agreeing on the right film came to 15 + 12 + 6, against a
threshold of 70. So nothing Wizdom ever returned could be accepted, every film
fell through to "below threshold, used anyway", and every subtitle in the
picker was labelled as a weak guess. A viewer reading 33 next to the best
Hebrew subtitle that exists for a film concludes the add-on cannot find
subtitles, and from what they were shown that is the right conclusion.

The missing piece was the largest one. Every candidate in that list is the
answer to a search for **one specific film**, by IMDb id - and that was worth
nothing in the sum. Three weak signals were being added up and the strong one
was being ignored. The scale is now a calibrated probability:

    100   the same file by hash, or the same release name
     85   the same group, source and codec
     70   the right title, same source and resolution, a different group
     55   the right title and the same source
     40   the right title and nothing else
      0   demonstrably the wrong episode

Measured against live Wizdom results, the same 32 candidates in the same
order: 100 for an identical name, 85 group+source+codec, 80 group+source, 70
source+resolution+codec, 55 source, 40 title only. Eight tests pin it, because
the old scale was wrong in a way no ordering test could ever have caught.

### Three real bugs found underneath it

**Two unknown resolutions counted as an agreement.** `source` and `codec` both
guard against "unknown"; `resolution` did not, so two names that fail to state
a resolution were credited 12 points for agreeing about nothing. It got worse
last night, when the parser started returning "unknown" rather than assuming
"sd".

**Every `.srt` lost its release group.** The group parser stripped video
extensions only, so `X.1080p.BluRay.x264-AMIABLE.srt` had no group at all -
in the one file type where the group matters most. The group is the strongest
signal there is: groups mux their own timings, so a subtitle made for a group
release fits it.

**A language suffix was read as the group.** Wizdom returns names like
`The.Shawshank.Redemption.1994.1080p.x264.YIFY-heb`, which parsed as a release
by a group called "heb". Subtitle decoration - language, `forced`, `sdh`, the
extension - now comes off before the group is looked for, repeatedly, because
it stacks: `...-GROUP.forced.heb.srt`.

Fixing that last one exposed a fourth: with `-heb` gone, `...x264.YIFY` has a
**dot-separated** group, which the parser did not recognise at all. It does
now, but only on a name that already carries a resolution, source or codec
token - otherwise the last word of every title becomes a release group and
"The.Office" is by a group called Office.

1011 tests, zip 423 KB.

### Confirmed on a real playback

Shawshank, in a real Kodi 21, with the subtitle cache emptied first so the
choice was actually made rather than read back:

    playing at speed 1
    file hash 4a3feee7f28684f4 computed in 950 ms
    best he subtitle: 100% (identical release name)
        The.Shawshank.Redemption.1994.1080p.x264.YIFY-heb
    Kodi: language heb, subtitles on

That 100% is the fix, not a coincidence. The file playing was
`The.Shawshank.Redemption.1994.1080p.x264.YIFY.mp4` and the subtitle is
`...x264.YIFY-heb`. Until today the `-heb` meant those two names were not
equal, so the strongest possible match - the same release - was invisible and
the subtitle was shown as an estimate. The decoration now comes off before the
comparison, and a certainty is reported as one.
