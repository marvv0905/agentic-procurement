"""Ephemeral research-run screenshots — the live "what the agent sees" feed.

The events store carries the step log; this store carries the latest browser
screenshot per order so the run console can show what the agent is actually
looking at. Frames live in memory, die with the process, and only the newest
frame per order is ever kept, so memory stays bounded no matter how long
research runs. Same design constraints as :mod:`procurement.events`: capture
is best-effort (a failed screenshot must never kill research) and the store
never grows without bound.

Thread-safe by design: research runs inside a background thread while the
Streamlit UI reads from the main thread.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

#: Same fence as the events store: past this many tracked orders, the order
#: with the oldest frame is evicted first.
MAX_TRACKED_ORDERS = 32

#: Hard cap on one stored frame (bytes). JPEG captures land far below this;
#: the cap exists so a pathological capture can never pin memory.
MAX_FRAME_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Frame:
    """The latest screenshot captured for one order's research run."""

    order_id: int
    ts: datetime
    data: bytes


_lock = threading.Lock()
_frames: dict[int, Frame] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def put(order_id: int, data: bytes) -> None:
    """Store the latest screenshot for an order (thread-safe, never raises).

    Empty or oversized captures are ignored — a bad frame is dropped, never
    an error, for the same reason the events store truncates instead of
    raising: this is a UI nicety, not the human record.
    """
    if not data or len(data) > MAX_FRAME_BYTES:
        return
    frame = Frame(order_id=order_id, ts=_now(), data=data)
    with _lock:
        _frames[order_id] = frame
        if len(_frames) > MAX_TRACKED_ORDERS:
            oldest = min(_frames, key=lambda oid: _frames[oid].ts)
            del _frames[oldest]


def latest(order_id: int) -> Frame | None:
    """The newest frame for the order, or ``None`` when nothing was captured."""
    with _lock:
        return _frames.get(order_id)


def clear(order_id: int) -> None:
    """Forget an order's frame (e.g. when its draft is discarded)."""
    with _lock:
        _frames.pop(order_id, None)