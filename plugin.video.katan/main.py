"""Plugin entry point. Kodi calls this for every plugin:// request."""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

from katan import router  # noqa: E402

if __name__ == "__main__":
    router.dispatch(sys.argv)
