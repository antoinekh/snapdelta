"""Store access from the web: names resolve through the store backend, which refuses names that leave the store."""

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable, Mapping

from snapdelta.errors import NotFoundError, SnapdeltaError
from snapdelta.output import Side
from snapdelta.profile import paired
from snapdelta.snapshot import StoredSource
from snapdelta.store import Store


class LruCache[K: Hashable, V]:
    """The last `size` values; the web server runs requests in threads, so a lock guards the order."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._values: OrderedDict[K, V] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: K, load: Callable[[], V]) -> V:
        with self._lock:
            if key in self._values:
                self._values.move_to_end(key)
                return self._values[key]
        value = load()
        with self._lock:
            self._values[key] = value
            self._values.move_to_end(key)
            while len(self._values) > self.size:
                self._values.popitem(last=False)
        return value

    def discard(self, key: K) -> None:
        with self._lock:
            self._values.pop(key, None)


class SnapshotCache:
    """Loaded snapshots for the pages that only read them: a saved snapshot never changes, it can only go away.

    Never give a cached snapshot to a compare: a compare changes `top_level` while it runs.
    """

    def __init__(self, size: int = 4) -> None:
        self._cache: LruCache[tuple[str, str], StoredSource] = LruCache(size)

    def load(self, store: Store, reference: str) -> StoredSource:
        # A delete from the CLI or from another request leaves a stale entry, so the store decides first.
        if not store.has_snapshot(reference):
            raise NotFoundError(f"no snapshot {reference!r} in {store.location}")
        return self._cache.get((store.location, reference), lambda: store.load_snapshot(reference))

    def side(self, store: Store, side: Side, pairs: Mapping[str, str] | None = None) -> StoredSource | None:
        """The snapshot that an output side names, with the names of the pairs of its profile; None when it is gone."""
        if side.snapshot is None:
            return None
        try:
            loaded = self.load(store, side.snapshot)
            if not pairs:
                return loaded
            key = (store.location, f"{side.snapshot} {sorted(pairs.items())}")
            return self._cache.get(key, lambda: paired(loaded, pairs))
        except SnapdeltaError:
            return None
