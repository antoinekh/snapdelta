"""Run independent fetches at the same time: the commands of one target, or the loads of several targets."""

import contextvars
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor

from .errors import FetchError


def run_parallel[T](
    tasks: Mapping[str, Callable[[], T]], max_workers: int | None = None
) -> tuple[dict[str, T], dict[str, Exception]]:
    """The results and the errors of the tasks by name, in task order; threads, because fetches wait on I/O."""
    if not tasks:
        return {}, {}
    with ThreadPoolExecutor(max_workers=max_workers or len(tasks)) as pool:
        # Each task runs in a copy of the caller's context, so that its log messages reach the log of the caller.
        futures = {name: pool.submit(contextvars.copy_context().run, task) for name, task in tasks.items()}
    results: dict[str, T] = {}
    failures: dict[str, Exception] = {}
    for name, future in futures.items():
        error = future.exception()
        if error is None:
            results[name] = future.result()
        elif isinstance(error, Exception):
            failures[name] = error
        else:
            raise error
    return results, failures


def fetch_parallel[T](tasks: Mapping[str, Callable[[], T]], max_workers: int | None = None) -> dict[str, T]:
    """The results of the tasks by name; raises FetchError, which names every failed task, when one fails."""
    results, failures = run_parallel(tasks, max_workers)
    if failures:
        raise FetchError(failures) from next(iter(failures.values()))
    return results
