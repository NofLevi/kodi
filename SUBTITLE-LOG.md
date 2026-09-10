# Subtitles: what was measured, what was built, what to do next

September 2026. Written to be picked up on another machine, so it starts with
what does *not* travel.

## Picking this up elsewhere

    git clone / git pull        branch: development
    python -m pytest tests      1300 tests, no Kodi needed

Three commits carry this work, newest last:

    814a9e2  One offset is the wrong model for a file cut differently
    0d473e9  The file hash stops being something the search waits for
    0303621  Looking for a split only when the file already scores well is circular

**Nothing here has been released.** `main` is behind `development` and the last
tag is v0.0.1. The next one would be v0.0.2 and would carry eight commits.

---

## How to cut a release, and where it goes

### The one rule

**A tag publishes. Nothing else does.**

    git push origin development     nothing happens
    git push origin main            nothing happens
    git push --follow-tags          .github/workflows/release.yml runs

`release.yml` fires on `push: tags: ["v*"]` and nothing else (plus a manual
button, for re-firing a publish that failed after the GitHub release was
already created). Pushing code never publishes anything, on any branch.

### The steps

    python tools/release.py --dry-run     say what would change, write nothing
    python tools/release.py               patch bump: 0.0.1 -> 0.0.2
    python tools/release.py --minor       0.0.1 -> 0.1.0

`release.py` runs the suite first and refuses on a red one. It bumps **both**
`addon.xml` files, writes `<news>` from the commit subjects since the last tag,
updates the README download links, and then prints exactly what to run next:

    git commit -am "Release 0.0.2"
    git push origin development
    git checkout main && git merge development
    git tag -a v0.0.2 -m "Release 0.0.2"
    git push origin main --follow-tags

**Annotated tags always** (`-a`). `--follow-tags` silently ignores a
lightweight tag, which looks exactly like forgetting to tag at all.

### What the tag then does, in order

1. Runs the whole test suite.
2. Checks the tag matches **all six** places the version appears — both
   `addon.xml` files, `<news>`, the README links, the repository index and the
   home-screen label — and refuses to publish if any disagree.
3. Builds `repo/` fresh from the commit being released. `repo/` is **not** in
   git: it used to be, and `git commit -am` stages only tracked files while
   every release produces a zip under a filename that never existed before, so
   the index could name a zip that was never pushed.
4. Cuts the GitHub release with both zips attached.
5. Uploads `repo/` to Cloudflare Pages with `wrangler pages deploy`.
6. **Polls the live index until it serves the tagged version.** That last step
   is the only automated proof a release reached the devices; everything before
   it can pass while the site still serves the previous version.

### Where it goes

| | |
|---|---|
| Stable channel | **noflevi.github.io/kodi** — what every device updates from |
| Test channel | **raw.githubusercontent.com/NofLevi/kodi/test-channel** — `update.channel = test`, expert settings |
| GitHub release | `NofLevi/kodi`, private, so the zips there are for you, not for Kodi |

The Cloudflare project is **deliberately disconnected from the repository**.
Cloudflare never sees a push, so it cannot log one, and a deployment exists if
and only if somebody published it. Do not reconnect it to "fix" anything — that
is what filled the deployment list with rows that were not builds.

The `pages.dev` hostname is baked into every installed copy of
`repository.katan`, so moving host strands every device unless the repository
add-on is republished at the *old* address first.

### Credentials it needs

* GitHub repository secrets `CLOUDFLARE_API_TOKEN` (Account → Pages → Edit) and
  `CLOUDFLARE_ACCOUNT_ID`. Secrets rather than a file on one laptop is exactly
  what lets a release be cut from either machine — **so the workflow route
  needs nothing copied across.**
* Gitignored `.cf-token` and `.cf-account`, only for `tools/deploy.py`. Those
  do have to be copied by hand if you want the manual escape hatch on the
  second machine.

### Before you tag

Run the slow checks, which are the ones that fire on a daily cron and a button
rather than on a push:

    python tools/e2e.py                     twenty live and offline checks
    python tools/e2e.py --only upgrade      a real 0.0.1 install upgraded

Standing instruction in this project: **do not run e2e, `upgrade.yml` or any
other workflow except before a release.** They are slow — a dozen live services
and a real release downloaded from the live site — and running them after every
commit is a run for every small change nobody reads.

### The escape hatches

    python tools/deploy.py            build and upload by hand, when the
                                      workflow failed after the release was
                                      already created. Tag afterwards.
    python tools/deploy.py --test     publish the working tree to the test
                                      channel
    python tools/deploy_android.py    push the zips to a device over adb

`deploy.py` skips the tag, the version check and the commit message that names
what was published — that is the price of it being the escape hatch.
`.github/workflows/testbuild.yml` publishes a test build, **by hand only**;
a test build is a deployment, and a deployment list where almost nothing was
asked for is what the disconnect was for.

---

### What is gitignored and will not be on the other machine

The measurement is the valuable part of this work and **none of it is in git**:

| Path | What it is | How to get it back |
|---|---|---|
| `subtitles.jsonl` | 422 surveyed titles, one JSON line each. Every number in this file came from it. | `python tools/survey_subtitles.py --count 400 --debrid` — hours, and it spends your debrid account |
| `.survey/` | the survey's own cache, kept separate so it never evicts what a real Kodi warmed | rebuilt on demand |
| `.kodi-test/` | portable Kodi 21.3, 232 MB | `python tools/setup_kodi.py` |
| `.cf-token`, `.cf-account` | Cloudflare Pages credentials for publishing | copy by hand, or rely on the GitHub secrets |

**Copy `subtitles.jsonl` across by hand.** It is 422 rows of live measurement
against real services and re-running it is expensive; `--report` reads it and
needs nothing else. If you only take one file that is not in git, take that one.

Rows written before this session lack three fields added during it —
`segments`, `fit_after` and `failed_bytes` — so those columns read as blank or
zero for older titles. That is age, not failure.

---

## The question this started from

*"I tried to watch this and it had no subtitles and offered only Italian."*

Which turned into: **we have never measured whether a subtitle actually fits.**
The old survey recorded the matcher's name score and called it accuracy, so
"23% got a subtitle" meant "23% had a candidate whose filename looked right".

## What the research found

I looked up how the rest of the world solves this.

Everyone reduces it to signal alignment: cut both sides into ~10 ms bins of "is
anyone talking", slide one past the other, take the shift with the most
overlap. [ffsubsync](https://ffsubsync.readthedocs.io/) does it with an FFT
against the video's audio through WebRTC voice-activity detection, in 20-30
seconds, most of that decoding audio. `subs/sync.py` already did the same thing
by another route — big-integer masks, chance-corrected overlap — in about
170 ms, because a *subtitle* reference needs no audio at all. **That half was
already done and is not the gap.**

[subliminal's](https://subliminal.readthedocs.io/en/1.1.1/api/score.html)
calibrated weights put `hash` at 137 against `release_group` at 11, and even
subliminal treats the name score as retrieval rather than proof of timing.
Bazarr, Plex and Emby all follow retrieval with a sync pass against the audio.
We cannot: an 8 GB remote file and a projector with a gigabyte of RAM is the
same trade this project already refused for extracting embedded subtitle tracks
over HTTP ranges.

The gap the research did find is [alass](https://github.com/kaegi/alass), and it
exists for one reason ffsubsync's own documentation concedes: *"difficulty with
videos that have breaks or splits in the middle"*. An advert break, a director's
cut, a recap left in — those produce **several** offsets in one file, and no
single shift fixes any of them. alass solves it with a dynamic program over
per-cue offsets where keeping the previous offset earns a `--split-penalty`
bonus (default 7, useful range 5-20). Published accuracy: 90% of lines within
400 ms, 88-98% success.

Our own survey had already found the same thing without naming it.

## What was built

### 1. `sync.fit_segments` — alass's idea at a fraction of the cost

Chops the subtitle into ~10-minute blocks and lets each block take its own
offset. Two guards, because re-timing piece by piece is the most dangerous
thing in the module — given enough pieces anything can be fitted to anything:

* **A block must clear an absolute floor**, not merely beat staying put.
  Measured on synthetic tracks a correctly placed block scores 1.0 and
  unrelated content never gets above 0.18; the floor sits at 0.5, in the gap.
  Without it, six blocks of the wrong episode each find a different spurious
  offset and assemble themselves into place.
* **The whole result is re-scored afterwards** and has to clear the same
  threshold a single shift does *and* beat the unsegmented fit, or the pieces
  are thrown away and the subtitle comes back exactly as downloaded.

It is nearly free when it is not needed: a block already sitting where the
global fit put it is never searched at all, so a healthy file pays one zero-lag
comparison per block. `SPLIT_MARGIN`, `MIN_SEGMENT_SCORE` and `SEGMENT_SETTLED`
are the calibration knobs and are constants with comments, not buried
conditionals.

Deliberately *not* alass's actual dynamic program: optimal split points over
1400 cues × 3600 candidate offsets is millions of interpreter operations on a
four-core A53. Fixed block boundaries find the break to within a block, which
is enough to re-time both sides of an advert break.

### 2. The file hash stopped being something the search waits for

The hash — a HEAD plus two 64 KB ranged requests, about a second — ran to
completion before the first provider was asked. That second is not a black
screen; it is a film **already playing** with no subtitles on it.

`video_hash_later` starts it and returns a getter. The nine providers that never
look at a hash are asking while it is still being read; the three that need it
resolve it inside their own task. One thread rather than a task in the search
pool, because a task waiting inside the pool for another task in the same pool
deadlocks the moment every worker is a waiter.

The test discriminates rather than merely passing: forced back to serial it
measures 0.82 s against a 0.75 s budget and fails.

### 3. The split search was gated behind the thing splits cannot do

This one is worth reading twice, because it was shipped, tested, benchmarked and
**completely dead**, and the survey would have reported it working as intended
forever.

`synchronise` refused to look for a split when the global confidence was below
`MIN_CONFIDENCE` (0.45). But **a split file cannot clear 0.45** — that is what a
split *is*, no single offset explaining the whole file. The three cases the
survey caught being 176 s, 129 s and 13 s out score 0.33, 0.10 and 0.07. Nought
of three could reach the code written for them.

A weak global fit is now the strongest reason to look for a split rather than a
reason to stop. What replaces the gate is the measurement described above: the
guard is on the output, not on the intent.

---

## What was measured

422 titles, stratified rather than random, `--debrid` so streams could be
hashed.

```
had sources                    254
had any candidate              180  (70%)
cleared the threshold           73  (28%)
GOT NOTHING AT ALL              74  (29%)
downloaded but would not parse  20  (11% of what was found)
```

### The 29% that got nothing is the biggest number on the board

```
anime episodes              33 of 69   (48%)
foreign-language episodes   20 of 43   (47%)
specials, season zero        6 of 16   (38%)
first episodes               9 of 35   (26%)
```

Half of all anime and half of all non-English drama get no subtitle whatsoever.
Ten times more viewers get **nothing** than get something mistimed.

### Only two providers are actually alive

```
opensubtitles_rest   1466 candidates
wizdom                582
```

SubSource is behind a login, Ktuvit needs an account, the OpenSubtitles API
needs a key, BSPlayer answers only when we have a hash.

### The hash, and why it was nearly deleted by mistake

25 titles of 180 had a hash match (13%):

```
11  the match was itself the winner   a perfect subtitle, right by construction
15  the match was in another language the only ruler this add-on owns
```

Of those 15 rulers, **7 exposed a subtitle that does not fit**:

```
The Good Doctor       fit 0.33   +176.4s   name score 77
Cosmos                fit 0.10   -129.5s   name score 73
Fauda                 fit 0.16    -93.5s   name score 83
Planet Earth          fit 0.13    -70.9s   name score 66
The Wire              fit 0.27    -58.1s   name score 62
House                 fit 0.12    +29.2s   name score 62
Money Heist           fit 0.07    -12.9s   name score 79
```

Every one of those is shown to the viewer today, unchanged, with a 62-83% label
beside it. Those are not drift — drift is a fraction of a second per minute and
is already corrected. Thirteen to a hundred and seventy-six seconds is the
signature of a different cut.

This is why "the hash only pays off 10% of the time, drop it" is the wrong
call, and it was nearly made: `reference_cues` accepts a hash match and nothing
else, so with no hash `sync.py` never runs, `fit_segments` never runs, and the
release name becomes the entire judgement with nothing able to contradict it.

### Calibration: the name score does not predict the fit

```
name score      titles   median fit   below 0.45
 70-89             3       0.10       3 (100%)
 55-69             7       0.63       3 (42%)
 under 55          1       0.64       0 (0%)
```

Eleven data points, so treat it as a signal rather than a measurement — but the
inversion has now shown up twice: the highest-scoring subtitles were the worst
fits.

---

## Things considered and deliberately not done

* **Aligning against the video's audio.** What ffsubsync, alass and Bazarr all
  prefer. Needs a demuxer and decoder for an 8 GB remote file on a gigabyte of
  RAM.
* **The consensus pair as a timing reference.** Planned, then cut after tracing
  it: `consensus.choose` already returns a member of the agreeing pair, so
  re-timing it onto its own partner shifts it by under two seconds toward
  something with no better claim to being right. Consensus gives a *subtitle*
  timeline, never the *video's*, so it cannot find a split against the file. It
  buys nothing.
* **Dropping the hash.** See above.
* **alass's dynamic program**, and **golden-section search for the framerate
  ratio** (ffsubsync `--gss`) — nine known ratios cover every real conversion
  and cost less than the search.
* **Re-weighting the matcher towards subliminal's numbers.** Retrieval is not
  the problem. Timing is.

---

## What to do next, ranked

### 1. Anime has the same three bugs the source layer already fixed

The one to start with: biggest single bucket, and **the fix is already written
and simply is not wired in.**

`opensubtitles_rest` asks for `imdbid-X / season-2 / episode-46`. That is
TMDB's numbering — the address this project has already documented as *"the one
nobody indexes"*, because TMDB folds a whole multi-year arc into one season
while Kitsu, AniDB and every release group number each cour from 1. Torrentio
returned nothing at all for that address and nine sources for the right one.
`wizdom` has it too: IMDb id plus season plus episode.

`tmdb.absolute_episode` and `tmdb.english_title` exist and are proven, and
`meta["absolute"]` is *already carried into the subtitle layer* —
`matcher.target_from` reads it and then nothing queries with it.

Target: 33 titles. It is reuse, not new code.

### 2. Add Podnapisi and SubDL

Both anonymous, no account, and a4kSubtitles carries both. Podnapisi is strong
precisely on Spanish, Turkish and Italian, which is the 47% foreign-language
hole and the one that matters at home.

Target: 20 titles.

### 3. The 20 downloads that will not parse

11% of everything found, all from one provider, concentrated in anime. The SSA
parser was supposed to have fixed this and did not. The survey now records the
first 60 bytes of a failed download (`failed_bytes`), so **one short run names
the cause** — nothing had ever recorded what actually arrived, and a parse
failure and an error page look identical from above.

### 4. Stop the picker showing a number it cannot support

It says "77% estimate" beside something 176 seconds out. Where sync has measured
a real fit, show that; where it has not, say *unverified* rather than inventing
a percentage.

### 5. Prove the splits, or delete them

Nobody has yet seen `fit_segments` repair a real subtitle, because until commit
`0303621` it could not run on one. Re-survey the seven misfits above with the
fixed code.

**Kill criterion, agreed in advance:** if it repairs fewer than about 2 of the
7, delete it. 120 lines for a 1-in-15 case is not worth carrying.

### 6. `meta["duration"]` is 0 on every title

So the report's "covers under 60% of runtime" check has **never once been able
to fire** — the same class as the Hebrew detector that never ran. Worth fixing
on its own, but the bigger prize behind it is that the file's true duration is
the one ruler that would be available 100% of the time instead of 13%.
`player.py:54` already reads it with `getTotalTime()`. A subtitle whose cues run
three minutes past the end of the file is provably wrong, with no reference and
no hash.

Note the honest caveat: this could not be validated offline, because the survey
has no real file duration and TMDB's nominal runtime is too coarse to separate a
43-minute web release from a 46-minute broadcast cut. It needs a real playback.
