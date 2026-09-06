"""xbmcgui stub: enough of ListItem, Window and the dialogs to unit test."""

DIALOG_ANSWERS = {"yesno": True, "select": -1, "ok": True}
NOTIFICATIONS = []


class InfoTagVideo(object):
    def __init__(self):
        self.data = {}

    def _set(self, key):
        def setter(*args):
            self.data[key] = args[0] if len(args) == 1 else args
        return setter

    def __getattr__(self, name):
        if name.startswith("set"):
            return self._set(name[3:].lower())
        raise AttributeError(name)


class ListItem(object):
    def __init__(self, label="", label2="", path="", offscreen=False):
        self.label = label
        self.label2 = label2
        self.path = path
        self.offscreen = offscreen
        self.art = {}
        self.info = {}
        self.properties = {}
        self.unique_ids = {}
        self.context = []
        self._tag = InfoTagVideo()

    def getVideoInfoTag(self):
        return self._tag

    def setArt(self, art):
        self.art.update(art)

    def setInfo(self, type, info):
        self.info.update(info)

    def setUniqueIDs(self, ids, default=None):
        self.unique_ids.update(ids)

    def setProperty(self, key, value):
        self.properties[key] = value

    def getProperty(self, key):
        return self.properties.get(key, "")

    def setLabel(self, label):
        self.label = label

    def setLabel2(self, label):
        self.label2 = label

    def getLabel(self):
        return self.label

    def setPath(self, path):
        self.path = path

    def getPath(self):
        return self.path

    def addContextMenuItems(self, items, replaceItems=False):
        self.context.extend(items)


class Window(object):
    PROPERTIES = {}

    def __init__(self, window_id=0):
        self.window_id = window_id

    def getProperty(self, key):
        return Window.PROPERTIES.get(key, "")

    def setProperty(self, key, value):
        Window.PROPERTIES[key] = value

    def clearProperty(self, key):
        Window.PROPERTIES.pop(key, None)


class _ControlList(object):
    def __init__(self, control_id):
        self.control_id = control_id
        self.items = []
        self.position = 0

    def reset(self):
        self.items = []
        self.position = 0

    def addItem(self, item):
        self.items.append(item)

    def addItems(self, items):
        self.items.extend(items)

    def getSelectedPosition(self):
        return self.position

    def selectItem(self, index):
        """Kodi's ControlList has this; the stub did not, so nothing could
        test what happens when a viewer picks a row other than the first."""
        self.position = int(index)

    def getSelectedItem(self):
        if 0 <= self.position < len(self.items):
            return self.items[self.position]
        return None

    def getListItem(self, index):
        return self.items[index]

    def size(self):
        return len(self.items)

    def setLabel(self, label):
        self.label = label

    def setVisible(self, visible):
        self.visible = visible


class WindowXML(object):
    def __init__(self, *args, **kwargs):
        self._properties = {}
        self._controls = {}
        self._focus = 0
        self.closed = False

    def getControl(self, control_id):
        return self._controls.setdefault(control_id, _ControlList(control_id))

    def setProperty(self, key, value):
        self._properties[key] = value

    def getProperty(self, key):
        return self._properties.get(key, "")

    def clearProperty(self, key):
        self._properties.pop(key, None)

    def setFocusId(self, control_id):
        self._focus = control_id

    def getFocusId(self):
        return self._focus

    def doModal(self):
        pass

    def close(self):
        self.closed = True

    def onInit(self):
        pass


class WindowXMLDialog(WindowXML):
    pass


class Dialog(object):
    def ok(self, heading, message):
        return DIALOG_ANSWERS["ok"]

    def yesno(self, heading, message, nolabel="", yeslabel="", autoclose=0):
        return DIALOG_ANSWERS["yesno"]

    def select(self, heading, list, autoclose=0, preselect=-1, useDetails=False):
        return DIALOG_ANSWERS["select"]

    def notification(self, heading, message, icon="", time=5000, sound=True):
        NOTIFICATIONS.append((heading, message))

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
