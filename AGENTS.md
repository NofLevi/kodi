# Working on Katan

Short, source-only context for anyone — person or agent — changing this code.
`CLAUDE.md` explains how the add-on fits together and why; this is the set of
rules that are easy to break without noticing. It is tracked in Git and must
never reach a device: it is not under `plugin.video.katan/`, so `build.py`
cannot package it, and `test_packaging.py` holds both halves.

## The environment, not the language

Kodi 21 runs **Python 3.8**, so nothing newer than 3.8 syntax compiles on a
television however well it runs here. The suite runs against stubs in
`tests/stubs/`, and those stubs have been wrong about Kodi's real API more
than once — `ACTION_SELECT_ITEM` is 7 and is the OK button, a modal window
silently refuses to open another over itself, and a plugin process is torn
down the moment it returns, so anything long-running belongs in
`background.py`. When a behaviour matters, verify it in a real Kodi rather
than in a passing test.

## Secrets

Nothing that authenticates anything goes in the repository, ever — no debrid
keys, no Cloudflare or GitHub tokens, no FTP passwords, no signed URLs. They
belong in the environment or in a gitignored file. The one deliberate
exception is the bundled TMDB key, which exists so films work with no setup
and is documented as public.

The repository is public, so the history is public too: a secret committed
once is burned even after a rewrite.

## What "done" means here

A change is finished when it is measured, not when it looks right. This
project's real defects were all found by pointing something at a live service
— an index registering one file hash against four unrelated titles, a rule
that ranked a Turkish drama below an English redub, a gate that made a code
path unreachable on exactly the cases it was written for. Quote numbers in the
commit message; the commit log is the record of what was actually checked.

Tests belong with the change. `python -m pytest tests` runs the whole suite in
about a minute and needs no Kodi.

## Branches and releases

Work on `development`. `main` moves only for a release, and only when the
owner asks. A release is `python tools/release.py`, then a merge to `main` and
an annotated tag — the tag is the only thing that publishes, through
`.github/workflows/release.yml`, to GitHub Pages.

Hosting must answer **ranged reads**. Kodi reads a zip's central directory at
the end of the file and then seeks to each member, so a host that answers
`Range` with the whole file makes every seek a full download; that is what
took Kodi down on Android when the repository was on Cloudflare Pages.

## Devices

`tools/install_ftp.py` puts a build on an Android box over the FTP server its
file manager offers. Prefer `--release`, which installs what the site
publishes: a box running an unpublished working tree produces bug reports
nobody can reproduce from a tag.
