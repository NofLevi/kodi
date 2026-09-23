"""One process-wide generation for every automatic or on-demand AI translation."""
import threading

_lock = threading.RLock()
_generation = [0]


def begin():
    """Supersede any older translation and return this job's generation."""
    with _lock:
        _generation[0] += 1
        return _generation[0]


def current(generation):
    with _lock:
        return generation == _generation[0]


def commit(generation, action):
    """Run a short write/player mutation only while generation is current."""
    with _lock:
        if generation != _generation[0]:
            return False, None
        return True, action()
