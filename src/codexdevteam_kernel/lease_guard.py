"""Maintain a HEAD lease while a bounded external invocation is running."""

from threading import Event, Lock, Thread
from typing import Callable

from .state import HeadLease, LeaseError, StateStore


class HeadLeaseGuard:
    """Renew a HEAD lease in the background and signal the first fencing error."""

    def __init__(self, store: StateStore, lease: HeadLease, *,
                 ttl_seconds: float = 30.0, renew_interval_seconds: float = 5.0,
                 on_renew: Callable[[HeadLease], None] | None = None):
        if ttl_seconds <= 0 or renew_interval_seconds <= 0 or renew_interval_seconds >= ttl_seconds:
            raise ValueError("lease TTL and renewal interval are invalid")
        self.store = store
        self._lease = lease
        self.ttl_seconds = ttl_seconds
        self.renew_interval_seconds = renew_interval_seconds
        if on_renew is not None and not callable(on_renew):
            raise ValueError("on_renew must be callable or null")
        self.on_renew = on_renew
        self.lost_event = Event()
        self._stop = Event()
        self._lock = Lock()
        self._failure: Exception | None = None
        self._thread: Thread | None = None

    def __enter__(self) -> "HeadLeaseGuard":
        self._renew()
        self._thread = Thread(target=self._run, name="codexdevteam-head-lease", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            # Finish any delayed SQLite renewal before invocation teardown returns.
            thread.join()

    def raise_if_lost(self) -> None:
        if self.lost_event.is_set():
            with self._lock:
                failure = self._failure
            raise LeaseError("HEAD lease was lost during runtime invocation") from failure

    def _run(self) -> None:
        while not self._stop.wait(self.renew_interval_seconds):
            try:
                self._renew()
            except Exception as exc:
                with self._lock:
                    self._failure = exc
                self.lost_event.set()
                return

    def _renew(self) -> None:
        with self._lock:
            self._lease = self.store.renew_head(self._lease, ttl_seconds=self.ttl_seconds)
            renewed = self._lease
        if self.on_renew is not None:
            self.on_renew(renewed)
