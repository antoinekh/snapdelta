import threading
import time

import pytest

from snapdelta.errors import FetchError, LoadError
from snapdelta.parallel import fetch_parallel, run_parallel


def sleeper(value: str, seconds: float = 0.2):
    def task() -> str:
        time.sleep(seconds)
        return value

    return task


def failing(message: str):
    def task() -> str:
        raise LoadError("R1", message)

    return task


def test_tasks_run_at_the_same_time_and_keep_their_order() -> None:
    start = time.monotonic()
    results = fetch_parallel({"isis": sleeper("i"), "bgp": sleeper("b"), "interfaces": sleeper("x")})
    assert time.monotonic() - start < 0.45
    assert list(results.items()) == [("isis", "i"), ("bgp", "b"), ("interfaces", "x")]


def test_max_workers_limits_the_threads() -> None:
    running: list[int] = []
    lock = threading.Lock()
    active = 0

    def task() -> None:
        nonlocal active
        with lock:
            active += 1
            running.append(active)
        time.sleep(0.05)
        with lock:
            active -= 1

    fetch_parallel({str(index): task for index in range(6)}, max_workers=2)
    assert max(running) == 2


def test_one_error_names_every_failed_task() -> None:
    with pytest.raises(FetchError) as caught:
        fetch_parallel({"isis": failing("timeout"), "bgp": sleeper("b", 0), "routes": failing("refused")})
    assert set(caught.value.failures) == {"isis", "routes"}
    assert "isis: R1: timeout" in str(caught.value) and "routes: R1: refused" in str(caught.value)
    assert isinstance(caught.value, LoadError)


def test_run_parallel_returns_the_results_and_the_failures() -> None:
    results, failures = run_parallel({"R1": sleeper("ok", 0), "R2": failing("down")})
    assert results == {"R1": "ok"}
    assert str(failures["R2"]) == "R1: down"


def test_no_task_gives_no_result() -> None:
    assert fetch_parallel({}) == {}
