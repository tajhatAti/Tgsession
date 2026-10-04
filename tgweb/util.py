"""Shared utilities: bounded LRU caches, media semaphore, small helpers."""
import asyncio
import html as html_lib
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

_ESC = html_lib.escape


class LRUCache:
    """Thread-unfriendly (single loop) bounded LRU cache for media bytes."""

    def __init__(self, max_items: int, max_bytes: int):
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._d: Dict[Any, Tuple[bytes, str]] = {}
        self._bytes = 0

    def get(self, k):
        v = self._d.pop(k, None)
        if v is not None:
            self._d[k] = v
        return v

    def _evict(self, k):
        v = self._d.pop(k, None)
        if v is not None:
            self._bytes -= len(v[0])

    def put(self, k, v: Optional[Tuple[bytes, str]]):
        if v is None:
            return
        if k in self._d:
            self._evict(k)
        self._d[k] = v
        self._bytes += len(v[0])
        while self._d and (self._bytes > self.max_bytes or len(self._d) > self.max_items):
            oldest = next(iter(self._d))
            self._evict(oldest)


thumb_cache = LRUCache(400, 48 * 1024 * 1024)
photo_cache = LRUCache(80, 48 * 1024 * 1024)
avatar_cache = LRUCache(300, 16 * 1024 * 1024)
dl_semaphore = asyncio.Semaphore(4)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def listify(x) -> list:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]
