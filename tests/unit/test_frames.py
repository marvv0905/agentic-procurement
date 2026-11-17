"""Unit tests for the ephemeral run-frame store."""

from __future__ import annotations

import threading

from procurement import frames


def test_put_keeps_only_the_newest_frame():
    frames.clear(1)
    frames.put(1, b"first")
    frames.put(1, b"second")
    frame = frames.latest(1)
    assert frame is not None
    assert frame.data == b"second"
    assert frame.order_id == 1


def test_frames_are_isolated_per_order():
    frames.clear(1)
    frames.clear(2)
    frames.put(1, b"a")
    frames.put(2, b"b")
    assert frames.latest(1).data == b"a"
    assert frames.latest(2).data == b"b"


def test_clear_forgets_a_frame():
    frames.put(3, b"x")
    frames.clear(3)
    assert frames.latest(3) is None


def test_empty_or_oversized_frames_are_ignored():
    frames.clear(4)
    frames.put(4, b"")
    assert frames.latest(4) is None
    frames.put(4, b"x" * (frames.MAX_FRAME_BYTES + 1))
    assert frames.latest(4) is None
    frames.put(4, b"ok")
    assert frames.latest(4).data == b"ok"


def test_tracked_orders_cap_evicts_oldest():
    count = frames.MAX_TRACKED_ORDERS + 5
    for i in range(count):
        frames.clear(i)
    for i in range(count):
        frames.put(i, bytes([i + 1]))
        assert frames.latest(i) is not None
    # The earliest orders were evicted once the fence was hit; the recent
    # ones (including the very last) survived.
    assert frames.latest(0) is None
    assert frames.latest(count - 1) is not None
    assert frames.latest(count - 1).data == bytes([count])


def test_put_is_thread_safe():
    frames.clear(5)
    per_thread = 50

    def spam():
        for _ in range(per_thread):
            frames.put(5, b"x")

    threads = [threading.Thread(target=spam) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert frames.latest(5) is not None
    assert frames.latest(5).data == b"x"