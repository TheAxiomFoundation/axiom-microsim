"""Request-byte LRU operations remain atomic during threaded eviction.

An absent ``get`` returns None; a read that found an entry returns its bytes,
even if another request wants to evict it. Completed operations never expose
more than the configured number of entries. The dictionary doubles force
thread switches at the same gaps ordinary Modal request threads can encounter.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from axiom_microsim.run import microsim as M
from axiom_microsim.run.microsim import _BoundedRequestCache


@pytest.mark.parametrize("operation", ["get", "getitem"])
def test_read_and_lru_touch_are_atomic_with_eviction(operation, monkeypatch):
    read = threading.Event()
    resume_read = threading.Event()
    writer_reached_read = threading.Event()
    errors: list[Exception] = []
    results: list[bytes | None] = []
    key = ("original",)
    reader: threading.Thread

    class ObservedLock:
        """Expose contention so the forced schedule needs no timing guesses."""

        def __init__(self):
            self.lock = threading.Lock()

        def __enter__(self):
            if not self.lock.acquire(blocking=False):
                writer_reached_read.set()
                self.lock.acquire()

        def __exit__(self, *args):
            self.lock.release()

    class PausedRead(OrderedDict):
        def _pause(self):
            if threading.current_thread() is reader:
                read.set()
                assert resume_read.wait(5), "reader was never released"

        def get(self, key, default=None):
            value = super().get(key, default)
            self._pause()
            return value

        def __getitem__(self, key):
            value = super().__getitem__(key)
            self._pause()
            return value

        def popitem(self, last=True):
            item = super().popitem(last=last)
            writer_reached_read.set()
            return item

    monkeypatch.setattr(M, "Lock", ObservedLock, raising=False)
    cache = _BoundedRequestCache(max_entries=1)
    cache[key] = b"original"
    cache._d = PausedRead(cache._d)

    def read_value():
        try:
            results.append(cache.get(key) if operation == "get" else cache[key])
        except Exception as exc:
            errors.append(exc)

    def evict_value():
        try:
            cache[("replacement",)] = b"replacement"
        except Exception as exc:
            errors.append(exc)

    reader = threading.Thread(target=read_value)
    writer = threading.Thread(target=evict_value)
    reader.start()
    try:
        assert read.wait(5), "reader never fetched the existing entry"
        writer.start()
        # The writer must either evict the entry (without a lock) or encounter
        # the reader's held lock. Only then can the reader touch its LRU entry.
        assert writer_reached_read.wait(5), "writer never reached the paused read"
    finally:
        resume_read.set()
        reader.join(5)
        if writer.ident is not None:
            writer.join(5)

    assert not reader.is_alive() and not writer.is_alive()
    assert errors == [], f"concurrent eviction interrupted an existing read: {errors!r}"
    assert results == [b"original"]
    assert len(cache) == 1
    assert cache.get(key) is None
    assert cache.get(("replacement",)) == b"replacement"


def test_threaded_request_cache_stress_never_raises_or_exceeds_cap(monkeypatch):
    first_read = threading.Event()
    resume_first_read = threading.Event()
    first_read_done = threading.Event()
    writer_reached_read = threading.Event()
    first_reader: threading.Thread

    class ObservedLock:
        def __init__(self):
            self.lock = threading.Lock()

        def __enter__(self):
            if not self.lock.acquire(blocking=False):
                writer_reached_read.set()
                self.lock.acquire()

        def __exit__(self, *args):
            self.lock.release()

    class YieldingDict(OrderedDict):
        def get(self, key, default=None):
            value = super().get(key, default)
            if threading.current_thread() is first_reader and not first_read_done.is_set():
                first_read.set()
                assert resume_first_read.wait(5), "first reader was never released"
            time.sleep(0)  # Switch after fetch, before the LRU touch.
            return value

        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            time.sleep(0)  # Switch after insertion, before capacity enforcement.

        def popitem(self, last=True):
            item = super().popitem(last=last)
            writer_reached_read.set()
            return item

    cap = 3
    monkeypatch.setattr(M, "Lock", ObservedLock, raising=False)
    cache = _BoundedRequestCache(max_entries=cap)
    for number in range(cap):
        cache[(number,)] = b"request"
    cache._d = YieldingDict(cache._d)
    start = threading.Barrier(4)
    errors: list[Exception] = []
    sizes: list[int] = []

    def write():
        try:
            start.wait(timeout=5)
            assert first_read.wait(5), "reader never fetched the initial entry"
            cache[(cap,)] = b"request"
            # Keep the evicted key absent until its paused read finishes.
            assert first_read_done.wait(5), "initial read never finished"
            for index in range(500):
                cache[(index % 7,)] = b"request"
                sizes.append(len(cache))
        except Exception as exc:
            errors.append(exc)

    def read(tag):
        try:
            start.wait(timeout=5)
            if tag == 1:
                try:
                    assert cache.get((0,)) == b"request"
                finally:
                    first_read_done.set()
            else:
                assert first_read_done.wait(5), "initial read never finished"
            for index in range(500):
                key = ((index + tag) % 7,)
                cache.get(key)
                _ = key in cache
                sizes.append(len(cache))
        except Exception as exc:
            errors.append(exc)

    first_reader = threading.Thread(target=read, args=(1,))
    threads = [first_reader, threading.Thread(target=write)]
    threads.extend(threading.Thread(target=read, args=(tag,)) for tag in (2, 3))
    for thread in threads:
        thread.start()
    try:
        assert writer_reached_read.wait(5), "writer never reached the paused read"
    finally:
        resume_first_read.set()
        for thread in threads:
            thread.join(10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == [], f"cache operations raised during eviction: {errors!r}"
    assert sizes and max(sizes) <= cap
    assert len(cache) <= cap


@settings(max_examples=60, deadline=None)
@given(
    cap=st.integers(min_value=1, max_value=5),
    operations=st.lists(
        st.tuples(st.sampled_from(["put", "get", "getitem", "contains"]), st.integers(0, 8)),
        max_size=100,
    ),
)
def test_request_cache_matches_bounded_lru_model(cap, operations):
    cache = _BoundedRequestCache(max_entries=cap)
    model: OrderedDict[tuple, bytes] = OrderedDict()
    for operation, number in operations:
        key = (number,)
        if operation == "put":
            value = str(number).encode()
            cache[key] = model[key] = value
            model.move_to_end(key)
            while len(model) > cap:
                model.popitem(last=False)
        elif operation in ("get", "getitem"):
            expected = model.get(key)
            if operation == "getitem":
                if expected is None:
                    with pytest.raises(KeyError):
                        cache[key]
                else:
                    assert cache[key] == expected
            else:
                assert cache.get(key) == expected
            if expected is not None:
                model.move_to_end(key)
        else:
            assert (key in cache) == (key in model)
        assert len(cache) == len(model) <= cap
        assert list(cache._d.items()) == list(model.items())
