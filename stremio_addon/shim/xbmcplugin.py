"""Kodi's `xbmcplugin`: directory calls that go nowhere.

The server never lists a Kodi directory, but Katan's UI modules import this
at the top, and some read sort-method constants while loading.
"""


def addDirectoryItem(*args, **kwargs):
    return True


def addDirectoryItems(*args, **kwargs):
    return True


def endOfDirectory(*args, **kwargs):
    pass


def setResolvedUrl(*args, **kwargs):
    pass


def setContent(*args, **kwargs):
    pass


def setPluginCategory(*args, **kwargs):
    pass


def addSortMethod(*args, **kwargs):
    pass


def __getattr__(name):
    # SORT_METHOD_* and friends: any number will do.
    if name.isupper():
        return 0
    raise AttributeError(name)
