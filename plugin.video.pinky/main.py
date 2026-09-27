"""Plugin entry point. Kodi calls this for every plugin:// request."""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "resources", "lib"))

from pinky import router  # noqa: E402

if __name__ == "__main__":
    try:
        router.dispatch(sys.argv)
    finally:
        # Let go of the shared worker threads before this invocation ends.
        #
        # `close_parallel` has always done the right thing and only the
        # *service* ever called it - but each plugin invocation is its own
        # interpreter with its own pool, and the one that holds a window runs
        # for as long as the window is open. So on quit:
        #
        #     main.py: trigger Monitor abort request
        #     main.py: script didn't stop in 5 seconds - let's kill it
        #
        # because `concurrent.futures` registers a hook that *joins* every
        # worker, and a worker abandoned at a search deadline is still inside
        # an HTTP call. Kodi then force-killed the interpreter and wedged: the
        # process stayed alive holding its files, Kodi never finished exiting,
        # and the next start found the old one still there and simply would
        # not open. Measured: `CRepositoryUpdater: busy playing` every two
        # minutes for nine minutes after "Application stopped".
        #
        # Nothing is lost by letting go. Every task already runs under a
        # wall-clock deadline, the results belong to a screen that is closing,
        # and the alternative is Kodi killing the interpreter anyway.
        try:
            from pinky import http
            http.close_parallel()
        except Exception:
            pass
