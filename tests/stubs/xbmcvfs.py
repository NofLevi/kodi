"""xbmcvfs stub. Paths are already real filesystem paths in tests."""
import os
import shutil


def translatePath(path):
    return path


def exists(path):
    return os.path.exists(path)


def mkdirs(path):
    if not os.path.isdir(path):
        os.makedirs(path)
    return True


def delete(path):
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def rmdir(path, force=False):
    try:
        shutil.rmtree(path) if force else os.rmdir(path)
        return True
    except OSError:
        return False


def listdir(path):
    dirs, files = [], []
    for name in os.listdir(path):
        (dirs if os.path.isdir(os.path.join(path, name)) else files).append(name)
    return dirs, files


class File(object):
    def __init__(self, path, mode="r"):
        self._handle = open(path, mode + "b" if "b" not in mode else mode)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def read(self):
        return self._handle.read()

    def write(self, data):
        return self._handle.write(data)

    def close(self):
        self._handle.close()
