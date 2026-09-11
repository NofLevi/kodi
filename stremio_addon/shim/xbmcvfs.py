"""Kodi's `xbmcvfs` over the plain filesystem."""
import os
import shutil


def translatePath(path):
    return path


def exists(path):
    return os.path.exists(path)


def mkdir(path):
    try:
        os.mkdir(path)
        return True
    except OSError:
        return False


def mkdirs(path):
    try:
        os.makedirs(path)
        return True
    except OSError:
        return os.path.isdir(path)


def delete(path):
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def rmdir(path, force=False):
    try:
        if force:
            shutil.rmtree(path)
        else:
            os.rmdir(path)
        return True
    except OSError:
        return False


def listdir(path):
    dirs, files = [], []
    for name in os.listdir(path):
        (dirs if os.path.isdir(os.path.join(path, name)) else files).append(name)
    return dirs, files


def copy(source, destination):
    try:
        shutil.copyfile(source, destination)
        return True
    except (IOError, OSError):
        return False


def rename(source, destination):
    try:
        os.replace(source, destination)
        return True
    except OSError:
        return False
