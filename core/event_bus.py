import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from loguru import logger


class EventBus:
    """Thread-safe publish/subscribe event bus."""

    def __init__(self, max_workers: int = 4):
        self._subscribers: dict[str, list] = defaultdict(list)
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def subscribe(self, event_type: str, callback) -> None:
        with self._lock:
            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback) -> None:
        with self._lock:
            if callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)

    def publish(self, event_type: str, data: dict = None) -> None:
        """Publish event asynchronously via thread pool."""
        data = data or {}
        with self._lock:
            callbacks = list(self._subscribers.get(event_type, []))
        for cb in callbacks:
            self._executor.submit(self._safe_call, cb, event_type, data)

    def publish_sync(self, event_type: str, data: dict = None) -> None:
        """Publish event synchronously (blocks until all callbacks complete)."""
        data = data or {}
        with self._lock:
            callbacks = list(self._subscribers.get(event_type, []))
        for cb in callbacks:
            self._safe_call(cb, event_type, data)

    def _safe_call(self, callback, event_type: str, data: dict) -> None:
        try:
            callback(data)
        except Exception as e:
            logger.error(f"EventBus callback error [{event_type}]: {e}")

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)
