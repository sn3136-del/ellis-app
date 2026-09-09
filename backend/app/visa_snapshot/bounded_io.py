"""Hard deadlines for read-only I/O, with a fixed cap on unfinished calls."""
from queue import Empty, Queue
import threading
import time


def call(fn, timeout_seconds: float, slots: threading.BoundedSemaphore):
    """A timed-out call retains its slot until it really exits.

    Threads are genuinely daemon threads, so a hung remote client cannot
    prevent process exit. A fixed semaphore prevents timed-out clients from
    accumulating; functions here must never mutate policy/database state.
    """
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not slots.acquire(timeout=remaining):
        raise TimeoutError('I/O deadline elapsed while waiting for capacity')
    result = Queue(maxsize=1)
    def run():
        try:
            result.put((True, fn()))
        except BaseException as exc:
            result.put((False, exc))
        finally:
            slots.release()
    thread = threading.Thread(target=run, name='freshness-bounded-io', daemon=True)
    try:
        thread.start()
    except BaseException:
        slots.release()
        raise
    try:
        ok, value = result.get(timeout=max(0.0, deadline - time.monotonic()))
    except Empty as exc:
        raise TimeoutError('I/O call exceeded its wall-clock deadline') from exc
    if not ok:
        raise value
    return value
