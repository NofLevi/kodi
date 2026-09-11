"""Kodi's `xbmcgui`: dialogs that show nothing, because a server has no screen."""
import threading

NOTIFICATION_INFO = "info"
NOTIFICATION_WARNING = "warning"
NOTIFICATION_ERROR = "error"
INPUT_ALPHANUM = 0
INPUT_NUMERIC = 1


class ListItem(object):
    def __init__(self, label="", label2="", path="", offscreen=False):
        self.label, self.label2, self.path = label, label2, path
        self.properties = {}

    def __getattr__(self, name):
        # setArt, setInfo, setProperty, addContextMenuItems... - all no-ops.
        def ignore(*args, **kwargs):
            return None
        return ignore


class Window(object):
    _PROPERTIES = {}
    _LOCK = threading.Lock()

    def __init__(self, window_id=0):
        self.window_id = window_id

    def getProperty(self, key):
        with Window._LOCK:
            return Window._PROPERTIES.get(key, "")

    def setProperty(self, key, value):
        with Window._LOCK:
            Window._PROPERTIES[key] = value

    def clearProperty(self, key):
        with Window._LOCK:
            Window._PROPERTIES.pop(key, None)


class WindowXML(Window):
    def __init__(self, *args, **kwargs):
        super(WindowXML, self).__init__()

    def doModal(self):
        pass

    def show(self):
        pass

    def close(self):
        pass


class WindowXMLDialog(WindowXML):
    pass


class Dialog(object):
    def ok(self, heading, message):
        return True

    def yesno(self, heading, message, *args, **kwargs):
        return False

    def select(self, heading, options, *args, **kwargs):
        return -1

    def notification(self, heading, message, icon="", time=5000, sound=True):
        pass

    def input(self, heading, defaultt="", type=0, option=0, autoclose=0):
        return ""


class DialogProgress(object):
    def create(self, heading, message=""):
        pass

    def update(self, percent, message=""):
        pass

    def iscanceled(self):
        return False

    def close(self):
        pass


class DialogProgressBG(DialogProgress):
    pass
