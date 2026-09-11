"""Just enough of Kodi's `xbmc` for Katan's engines to run inside a server.

Unlike the test stubs in tests/stubs, nothing here records anything. A server
runs for weeks, and a list that keeps every log line is a memory leak.
"""
import logging
import time

LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR, LOGFATAL = 0, 1, 2, 3, 4
ISO_639_1 = 0
ISO_639_2 = 1

_logger = logging.getLogger("katan")
_LEVELS = {LOGDEBUG: logging.DEBUG, LOGINFO: logging.INFO,
           LOGWARNING: logging.WARNING, LOGERROR: logging.ERROR,
           LOGFATAL: logging.CRITICAL}


def log(message, level=LOGDEBUG):
    _logger.log(_LEVELS.get(level, logging.INFO), "%s", message)


def sleep(milliseconds):
    time.sleep(max(0, milliseconds) / 1000.0)


def executebuiltin(command, wait=False):
    pass


def getInfoLabel(label):
    return ""


def getLanguage(fmt=ISO_639_1, region=False):
    # TMDB metadata in Hebrew, as the Kodi add-on shows it.
    return "he"


def convertLanguage(language, format=ISO_639_1):
    names = {"hebrew": "he", "english": "en", "arabic": "ar",
             "french": "fr", "german": "de", "russian": "ru",
             "spanish": "es"}
    return names.get((language or "").strip().lower(), "")


def getCondVisibility(condition):
    return False


def executeJSONRPC(request):
    return '{"result":"OK"}'


class Monitor(object):
    def abortRequested(self):
        return False

    def waitForAbort(self, timeout=0):
        time.sleep(max(0, timeout or 0))
        return False


class Player(object):
    def __init__(self, *args, **kwargs):
        pass

    def isPlaying(self):
        return False

    def play(self, *args, **kwargs):
        pass

    def stop(self):
        pass

    def setSubtitles(self, path):
        pass

    def showSubtitles(self, visible):
        pass

    def getAvailableSubtitleStreams(self):
        return []

    def getTime(self):
        return 0.0

    def getTotalTime(self):
        return 0.0


class Keyboard(object):
    def __init__(self, default="", heading="", hidden=False):
        self._text = default

    def doModal(self):
        pass

    def isConfirmed(self):
        return False

    def getText(self):
        return self._text


class Actor(object):
    def __init__(self, name="", role="", order=0, thumbnail=""):
        self.name, self.role, self.order, self.thumbnail = name, role, order, thumbnail
