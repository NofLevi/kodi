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

TorBox and TMDB are both connected and working. Films and episodes play.

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

**Worth knowing:** Kodi writes every declared setting into its own file the
first time it loads an add-on, so an install that already exists keeps the
values it has. This changes what a *fresh* install gets. Tools → Performance
profile applies the lean set to an existing one.

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
