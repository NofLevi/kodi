"""Subtitle service entry point.

Kodi calls this with action=search|manualsearch|download when the user opens
the subtitle dialog during playback.
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
