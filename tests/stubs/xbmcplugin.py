"""xbmcplugin stub that records what a handler produced."""
SORT_METHOD_NONE = 0
SORT_METHOD_LABEL = 1
SORT_METHOD_VIDEO_YEAR = 2

ITEMS = []
CONTENT = {}
ENDED = []
RESOLVED = []


def reset():
    del ITEMS[:]
    del ENDED[:]
    del RESOLVED[:]
    CONTENT.clear()


def addDirectoryItem(handle, url, listitem, isFolder=False, totalItems=0):
    ITEMS.append((url, listitem, isFolder))
    return True


def addDirectoryItems(handle, items, totalItems=0):
    ITEMS.extend(items)
    return True


def setContent(handle, content):
    CONTENT[handle] = content


def addSortMethod(handle, sortMethod, label2Mask=""):
    pass


def endOfDirectory(handle, succeeded=True, updateListing=False, cacheToDisc=True):
    ENDED.append((handle, succeeded))


def setResolvedUrl(handle, succeeded, listitem):
    RESOLVED.append((handle, succeeded, listitem))
