"""Subtitle service entry point.

Declared as the add-on's `xbmc.subtitle.module` library, and Kodi does not
actually run it. When a subtitle module belongs to an add-on that is also a
video plugin, Kodi's subtitle dialog calls
`plugin://plugin.video.katan/?action=search&languages=...`, and resolves that
plugin path to main.py. So the real path in is router.dispatch, which
recognises a subtitle request and hands it here.

This file stays because the extension point requires a library that exists,
and because a Kodi that does call it directly will get the same behaviour.
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

from katan import kodi  # noqa: E402

if __name__ == "__main__":
    try:
        from katan.subs import service
        service.dispatch(sys.argv)
    except Exception:
        kodi.log_exception("subtitle service failed")
