import time
import threading


class ElementCache:
    """TTL-based cache for pywinauto UI element wrappers."""

    def __init__(self, ttl_seconds: float = 30.0):
        self._cache: dict[str, tuple] = {}  # key -> (element, timestamp)
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            if key in self._cache:
                element, ts = self._cache[key]
                if time.time() - ts < self._ttl:
                    return element
                del self._cache[key]
        return None

    def put(self, key: str, element) -> None:
        with self._lock:
            self._cache[key] = (element, time.time())

    def invalidate(self, key: str = None) -> None:
        with self._lock:
            if key:
                self._cache.pop(key, None)
            else:
                self._cache.clear()

    def get_or_find(self, key: str, finder):
        element = self.get(key)
        if element is None:
            element = finder()
            self.put(key, element)
        return element
