# snapdelta-example

The smallest complete snapdelta plugin. Read it to see how a plugin works, then copy this directory to start your own. For a full project step by step (two vendors, an inventory, settings), see [docs/getting-started.md](../../docs/getting-started.md).

## Read it in this order

| File | What it shows |
|---|---|
| `src/snapdelta_example/__init__.py` | How the parts fit together |
| `src/snapdelta_example/models.py` | The diffsync models: identifiers, compared attributes, a child model, a field kept but never compared |
| `src/snapdelta_example/source.py` | `ExampleSource`, the source `example`: reads JSON files, fills the models, then calls `finish_load()` |
| `src/snapdelta_example/live.py` | `ExampleLiveSource`, the source `example-live`: fetches the same data from a target (`fetches = True`), lists its targets, and reads its settings (`ExampleLiveSettings`) |
| `pyproject.toml` | The entry points `example` and `example-live` in the group `snapdelta.sources`: how snapdelta finds the sources |

## The data

| Directory | Content |
|---|---|
| `samples/` | Two JSON files of the device R1, before and after a change, for the source `example` |
| `devices/` | Three simulated devices, R1, R2 and EDGE1, for the source `example-live`; `[sources.example-live] data_dir` in `snapdelta.toml` sets another directory |
| `inventory/` | A Nornir inventory of the three devices, used by the tests of snapdelta |

## Try it

From the root of the repository:

```bash
# Files that a person gives: R1 before and after a change
uv run snapdelta --store /tmp/snapdelta-demo snapshot examples/minimal-plugin/samples/r1-before.json --source example --label before
uv run snapdelta --store /tmp/snapdelta-demo snapshot examples/minimal-plugin/samples/r1-after.json --source example --label after
uv run snapdelta --store /tmp/snapdelta-demo diff R1@before R1@after

# A source that fetches: every simulated device, then two of them compared
uv run snapdelta --store /tmp/snapdelta-demo snapshot --source example-live --all-targets --label live
uv run snapdelta --store /tmp/snapdelta-demo diff R1@live R2@live
```
