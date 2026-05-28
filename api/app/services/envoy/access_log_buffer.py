"""In-memory ring buffer for envoy HTTP access log lines, keyed by node_id.

Envoy streams access logs to us over gRPC ALS (see als_server.py). The
billing pipeline consumes them and writes UsageLog rows, but the raw
"who hit what with which status / how long" view is useful for operators
debugging routing/auth issues — especially for remote envoys where we
have no envoy.log file to tail.

The buffer is process-local and bounded; restarting the api drops history.
That's intentional: this is for live tailing, not auditing (UsageLog is
the system of record).
"""
from __future__ import annotations

from collections import deque
from threading import Lock

# Per-node ring buffer size. ~500 entries × ~200 bytes ≈ 100 KiB per node —
# fine even with dozens of nodes.
MAX_LINES_PER_NODE = 500

_buffers: dict[str, deque[str]] = {}
_lock = Lock()


def append(node_id: str, line: str) -> None:
    if not node_id:
        return
    with _lock:
        buf = _buffers.get(node_id)
        if buf is None:
            buf = deque(maxlen=MAX_LINES_PER_NODE)
            _buffers[node_id] = buf
        buf.append(line)


def tail(node_id: str, n: int) -> list[str]:
    with _lock:
        buf = _buffers.get(node_id)
        if not buf:
            return []
        if n >= len(buf):
            return list(buf)
        return list(buf)[-n:]


def clear(node_id: str) -> None:
    with _lock:
        _buffers.pop(node_id, None)
