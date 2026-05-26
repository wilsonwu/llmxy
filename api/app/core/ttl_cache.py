"""Tiny in-process LRU+TTL cache. No external deps.

Used on hot paths (ext_authz) where we want sub-microsecond lookups for
ApiKey/User metadata and we don't want to pay the cost of a Redis RTT
on every request.

Single-threaded async assumption: FastAPI runs one event loop per
process, all coroutines yield cooperatively. We don't need a lock here —
the only mutations (get/set/pop) are short synchronous dict ops between
awaits.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    __slots__ = ("_data", "_maxsize", "_ttl")

    def __init__(self, maxsize: int, ttl_seconds: float) -> None:
        self._data: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl_seconds

    def get(self, key: K) -> V | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: K, value: V) -> None:
        expires_at = time.monotonic() + self._ttl
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = (expires_at, value)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def pop(self, key: K) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
