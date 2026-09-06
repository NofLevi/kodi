"""Background service entry point.

One thread, mostly asleep. It owns three jobs:
  * warming the home rows so opening the add-on is a database read
  * scrobbling playback to Trakt and firing the Up Next prompt
  * pruning the cache once a day
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

from katan import kodi  # noqa: E402

if __name__ == "__main__":
    try:
        from katan import background
        background.run()
    except Exception:
        kodi.log_exception("service crashed")
