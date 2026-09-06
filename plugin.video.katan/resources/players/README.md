# The TMDb Helper player file

`katan.json` lets [TMDb Helper][helper] play through Katan, so a skin built
around TMDb Helper can use this add-on as its source picker without knowing
anything about it.

Copy it into TMDb Helper's own player directory:

    userdata/addon_data/plugin.video.themoviedb.helper/players/katan.json

Then pick Katan in TMDb Helper's player settings.

## What each entry does

`play_movie` and `play_episode` are the automatic path: Katan searches, ranks
and starts the best source without asking anything. `search_movie` and
`search_episode` point at the same content but force the picker open, which is
the "choose a source" entry TMDb Helper offers alongside play.

`is_resolvable` is true because Katan hands Kodi a final playable URL rather
than another plugin path.

## Keeping it honest

The four URLs use real routes with real parameter names, and
`test_addon_integrity.py` fails if any of them stops matching a registered
route. A player file that quietly points at a route that no longer exists is
exactly the kind of thing nobody notices until a skin shows an error, so it is
checked rather than trusted.

[helper]: https://github.com/jurialmunkey/plugin.video.themoviedb.helper
