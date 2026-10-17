# Parallel loads

A load mostly waits: on an API, an SSH session, a NETCONF reply, a database. snapdelta runs these waits at the same time at two levels:

| Level | What runs at the same time | Who starts it |
|---|---|---|
| Inside one target | the fetches of one load: for example the interfaces, the IS-IS and the BGP state of one router | the plugin, in its `load()`, with `fetch_parallel()` |
| Across targets | the loads of several targets: one snapshot per target | snapdelta: the CLI (`--target` repeated, `--all-targets`, `--jobs`) and the web page "Take snapshot" |

Both levels use threads (`concurrent.futures.ThreadPoolExecutor`) by default, because the work is I/O, not computation; a [runner](#runners) can run the loads of several targets in processes or in the workers of a plugin instead. They combine: 4 targets at the same time, each with 4 fetches at the same time, gives up to 16 open requests.

## Inside one target: `fetch_parallel()`

diffsync itself does not load in parallel: `load()` is a plain method of your source, and the models go into the diffsync store with `add()`. So split `load()` in two steps:

1. fetch the raw data of every part at the same time, with `fetch_parallel()`;
2. parse the results and add the models, in one thread, after all the fetches.

```python
from snapdelta.parallel import fetch_parallel
from snapdelta.source import Source


class RouterSource(Source):
    source = "router"
    fetches = True

    def load(self) -> None:
        raw = fetch_parallel(
            {
                "interfaces": lambda: self.run("show interface json"),
                "isis": lambda: self.run("show isis neighbor json"),
                "bgp": lambda: self.run("show bgp neighbors json"),
            }
        )
        loaded = self.add_interfaces(raw["interfaces"]) + self.add_isis(raw["isis"]) + self.add_bgp(raw["bgp"])
        self.finish_load(self.target, loaded)
```

- The result is a `dict` in the order of the tasks, so the load gives the same snapshot on each run.
- When tasks fail, `fetch_parallel()` waits for all the others, then raises one `FetchError` (a `LoadError`) that names every failed task: `parallel fetch: isis: R1: timeout; bgp: R1: connection refused`. Its `failures` attribute maps each failed task to its exception. The snapshot is not saved: a partial load would show false differences.
- `max_workers` limits the threads (default: one per task). Limit it when the target accepts few sessions at the same time.
- A second step can also run in parallel: fetch the list of BGP peers first, then the routes of each peer with a second `fetch_parallel()` call.
- `run_parallel()` is the same without the exception: it returns the results and the failures, for a load that can keep the parts that worked.

Keep the fetch tasks free of side effects on the source: they only return data. Do the `add()` calls in `load()` after `fetch_parallel()` returns.

## Across targets: several snapshots at once

### CLI

```bash
snapdelta snapshot --source router --target R1 --target R2 --target R3 --label before
snapdelta snapshot --source router --all-targets --jobs 8 --label before
```

- `--target` can repeat; duplicates are ignored.
- `--all-targets` takes every target that the source lists with `targets()` (and adds the `--target` names).
- `--jobs N` is the number of targets loaded at the same time (default 4).
- Each target is loaded and saved on its own: a failed target does not stop the others. The command prints each saved snapshot, then `error: <target>: <reason>` for each failure, and exits with `2` when one target failed.
- Several targets need a source that fetches (`fetches = True`): input files belong to one target.

### Web UI

On "Take snapshot", search the targets of the source and check them: each checked target moves to "Selected". A name that the source does not list can be typed and selected ("Use NAME"). "All targets" takes every target of the source. The snapshot then runs in the background of the web server, and its page follows each target live: queued, running, saved with a link to its snapshot, or failed with its error; **Retry the failed** takes the failed targets again. The page can close: Activity keeps the run and its log. The web uses `snapshot.jobs` targets at the same time (4 by default).


### Python

```python
from snapdelta.settings import load_settings
from snapdelta.store import open_store_from
from snapdelta.take import snapshot_targets

settings = load_settings()  # snapdelta.toml, SNAPDELTA_STORE and the defaults, as the CLI reads them
store = open_store_from(settings)
taken, failures = snapshot_targets(store, settings, "router", all_targets=True, label="before")
for target, item in taken.items():
    print(target, item.reference, item.warnings)
for target, message in failures.items():
    print(target, message)
```

## Runners

A runner runs the loads of a snapshot. `[snapshot] runner` chooses it, for the CLI, the web UI, the API and MCP:

| Runner | How the loads run | Choose it when |
|---|---|---|
| `threads` (default) | in threads of the process, `jobs` at the same time | the loads wait on the network, as most do |
| `processes` | each load in a process of a pool of `jobs` processes | a plugin parses much (one CPU per load), or its library is not thread-safe |
| a plugin runner | as the plugin runs them: a queue and its workers, another machine | the loads must run where the devices are reachable, or outlive the web server |

```toml
[snapshot]
runner = "processes"
jobs = 8

[runners.processes]
start_method = "spawn"   # or "forkserver", faster, on Linux only
```

A task is data, not a function: the source, the target, the label and the capture time (`snapdelta.runners.LoadTask`). So a runner can send it to another process, where `snapdelta.runners.execute(task, settings, store)` loads the target with the plugins, the settings and the store found there, and gives back a `LoadOutcome`: the reference of the saved snapshot or the error, and the log of the load. snapdelta waits for every outcome, then records the run in Activity with the log of each load, as with threads.

- Each process of `processes` reads the settings file again and opens the store again by its location. It sees the installed plugins only, not the ones registered in code with `register_plugin()`.
- The workers of a plugin runner need the plugins and the same store: a file store on one machine does not fit, a shared database such as the [PostgreSQL store](stores.md#postgresql) does.

### Add a runner

A plugin adds a runner with a subclass of `snapdelta.runners.Runner` in the entry point group `snapdelta.runners`. Its class method `run(tasks, settings, store, jobs, options, progress)` returns the `LoadOutcome` of each task by target, and calls `progress(target, Progress("running"))` when a load starts and `Progress("saved", reference=...)` or `Progress("failed", error=...)` when it ends, so that the page of the web UI follows each target. `options` is its `[runners.NAME]` table, typed by its `settings_class`, a subclass of `snapdelta.runners.RunnerSettings`. `snapdelta plugins new NAME --kind runner` writes one that runs the loads one after the other, to start from.

## Stores and parallel saves

Each target saves its snapshot when its load ends, from its own thread or process.

- File store: each file is written to a temporary file, then renamed; two targets never write the same file.
- SQLite store: each save opens its own connection; a writer waits up to 30 s for the lock (`BUSY_TIMEOUT_S`) instead of failing.

See [stores.md](stores.md) for the store backends.

## Choose the numbers

- Start with the default (`--jobs 4`, one thread per fetch) and raise `--jobs` while the targets and the network keep up.
- Lower `max_workers` in `fetch_parallel()` when a target limits its sessions (SSH `MaxSessions`, NETCONF session limits, API rate limits).
- The total number of open requests is about `jobs × fetches per target`.
