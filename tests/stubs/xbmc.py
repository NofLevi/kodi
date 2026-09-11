"""Minimal xbmc stub so the add-on can be imported and unit tested off-device."""
LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR, LOGFATAL = 0, 1, 2, 3, 4
ISO_639_1 = 0
ISO_639_2 = 1

LOG = []
EXECUTED = []


def log(message, level=LOGDEBUG):
    LOG.append((level, message))


def sleep(milliseconds):
    pass


def executebuiltin(command, wait=False):
    EXECUTED.append(command)


def getInfoLabel(label):
    return {"System.BuildVersion": "21.3 (21.3.0) Git:Omega"}.get(label, "")


def getLanguage(fmt=ISO_639_1, region=False):
    return "en"


def convertLanguage(language, format=ISO_639_1):
    # Kodi knows every ISO-639 name. This knows the handful the tests use,
    # plus a couple that are neither Hebrew nor English, so a test can check
    # that a language the viewer explicitly asked for is not dropped.
    names = {"hebrew": "he", "english": "en", "arabic": "ar",
             "french": "fr", "german": "de", "russian": "ru",
             "spanish": "es"}
    return names.get((language or "").strip().lower(), "")


def getCondVisibility(condition):
    return False


JSONRPC_CALLS = []


# Tests set this to control what Player.GetProperties returns.
JSONRPC_RESULTS = {}


def executeJSONRPC(request):
    """Record the call, and answer from JSONRPC_RESULTS when a test set one."""
    import json
    JSONRPC_CALLS.append(request)
    try:
        method = json.loads(request).get("method", "")
    except ValueError:
        method = ""
    if method in JSONRPC_RESULTS:
        return json.dumps({"id": 1, "jsonrpc": "2.0",
                           "result": JSONRPC_RESULTS[method]})
    return '{"result":"OK"}'


class Monitor(object):
    def abortRequested(self):
        return False

    def waitForAbort(self, timeout=0):
        return True

    def onSettingsChanged(self):
        pass


class Player(object):
    def __init__(self, *args, **kwargs):
        self._playing = False

    def isPlaying(self):
        return self._playing

    def getPlayingFile(self):
        return getattr(self, "_file", "")

    def getTime(self):
        return 0.0

    def getTotalTime(self):
        return 0.0

    # What Player.play was asked to open. A plugin run from a context menu
    # has no handle to resolve to and has to start playback itself, and
    # there is no other way to see that it did.
    PLAYED = []

    def play(self, *args, **kwargs):
        Player.PLAYED.append((args, kwargs))

    def stop(self):
        pass

    # Where seekTime was asked to go, for the same reason as PLAYED.
    SEEKS = []

    def seekTime(self, seconds):
        Player.SEEKS.append(seconds)

    def setSubtitles(self, path):
        pass

    def getAvailableSubtitleStreams(self):
        return []

    def setSubtitleStream(self, index):
        pass

    def showSubtitles(self, visible):
        pass


class Keyboard(object):
    TEXT = ""
    CONFIRMED = False

    def __init__(self, default="", heading="", hidden=False):
        self._text = default

    def doModal(self):
        pass

    def isConfirmed(self):
        return Keyboard.CONFIRMED

    def getText(self):
        return Keyboard.TEXT


class Actor(object):
    def __init__(self, name="", role="", order=0, thumbnail=""):
        self.name, self.role, self.order, self.thumbnail = name, role, order, thumbnail
