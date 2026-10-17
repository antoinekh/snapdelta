# Stores: where snapshots and diffs are saved

snapdelta saves every snapshot, every diff output and every run in a store. Two backends are built in and a third is a package of this repository; all keep the same JSON documents, so you can change the backend without losing data.

| Backend | Location | Choose it when |
|---|---|---|
| File store (`FileStore`) | a directory: one JSON file per snapshot and per output | you want to read the files, back them up with any tool, or keep them in git (private repositories only) |
| SQLite store (`SqliteStore`) | one SQLite file | you have many targets and snapshots, or several processes of one machine write at the same time |
| PostgreSQL store (`snapdelta-postgres`) | a PostgreSQL database | several servers share the store, or the database is where your team keeps its data and its backups |

## Choose the store

The CLI and the web UI take the same option:

```bash
snapdelta --store /data/snapdelta ...                    # file store in this directory
snapdelta --store sqlite:///data/snapdelta.db ...        # SQLite store in this file
snapdelta-web --store sqlite:///data/snapdelta.db
```

Without `--store`, snapdelta uses the environment variable `SNAPDELTA_STORE`, else `[store] location` of the [settings file](settings.md), else the directory `~/.local/share/snapdelta` (file store). Set the variable once to use the same store everywhere:

```bash
export SNAPDELTA_STORE=sqlite:///data/snapdelta.db
snapdelta info
```

`sqlite://` is followed by the path of the file: `sqlite:///data/snapdelta.db` is the absolute path `/data/snapdelta.db`, `sqlite://snapdelta.db` is relative to the current directory. snapdelta creates the file and its tables on first use.

### PostgreSQL

The PostgreSQL backend is the package `snapdelta-postgres`, a plugin of the group `snapdelta.stores` (`pip install snapdelta-postgres`). The location is a libpq URI without its password: snapdelta shows the location in its pages and its runs, so it refuses a location with a password. Give the password in the settings, or in `PGPASSWORD` or `~/.pgpass`:

```toml
[store]
location = "postgresql://snapdelta@db.example.net:5432/snapdelta?sslmode=require"

[stores.postgresql]
password = { env = "SNAPDELTA_DB_PASSWORD" }   # any secret backend
connect_timeout_s = 10
pool_size = 4                                   # the connections kept open, for the requests at once
```

The store creates its tables `snapdelta_snapshots`, `snapdelta_outputs` and `snapdelta_runs` at its first use, with the columns of the SQLite store and the documents as `json`, which keeps the order of their keys. The database user needs the right to create tables. `make test-postgres` runs its tests against a server in a container.

## Names

Both backends use the same names, so a command works the same way on both:

- a snapshot is `TARGET/NAME`, where `NAME` is the UTC capture time and the label: `R1/20261012T130856Z-before`;
- `TARGET@latest` is the newest snapshot of a target, and `TARGET@LABEL` the newest with that label (`R1@before`);
- a name alone (`20261012T130856Z-before`) works when only one target has it;
- a diff output is `<UTC time>-<left target>-vs-<right target>`: `20261012T130927Z-R1-vs-R2`; a second output of the same pair in the same second gets `.2` after the time (`20261012T130927Z.2-R1-vs-R2`), and so on.

Unsafe characters in a target or a label become `_`. A name with `..` or another character outside letters, digits, `_`, `.` and `-` is never found, so no name can point outside the store.

## File store layout

```
<store>/
  snapshots/
    R1/
      20261012T130856Z-before.json
      20261012T134210Z-after.json
    R2/
      ...
  outputs/
    20261012T130927Z-R1-vs-R2.json
```

- Each file is written to a temporary file first, then renamed, so an interrupted write never leaves a broken file.
- The files are readable JSON. A snapshot holds `schema`, `target`, `source`, `label`, `captured_at`, `created_at`, `versions`, `inputs` (names and SHA-256 of the input files), `loaded` (the models), `unmapped`, `warnings`, and `data` (the diffsync data of the load). An output holds `schema`, `created_at`, `versions`, `left` and `right` (target, source, label, capture time, snapshot reference), `compared`, `skipped`, `summary` and `diff` (diffsync `Diff.summary()` and `Diff.dict()`).
- Long lists read only the directory and file names: the list of targets and of the snapshots of a target never opens a file.

## SQLite store layout

```sql
create table snapshots (reference text primary key, target text not null, captured_at text not null,
                        label text, source text not null, body text not null);
create table outputs (name text primary key, created_at text not null, left_target text not null,
                      right_target text not null, body text not null);
```

- `body` is the same JSON document as the file of a file store.
- Indexes on the target and on the creation time keep the lists fast with many rows.
- Each call opens its own connection, and a busy timeout makes a parallel writer wait instead of failing.
- You can query the file with any SQLite tool, for example `sqlite3 /data/snapdelta.db "select reference from snapshots where target = 'R1'"`.

## Formats and versions

Each document has a `schema` number. This version writes and reads schema 2.

## Clean up

The same commands work on both backends:

```bash
snapdelta info                                     # backend, location, targets, snapshots, outputs, size
snapdelta delete snapshot R1@latest R2/20261012T130856Z-before
snapdelta delete output 20261012T130927Z-R1-vs-R2
snapdelta prune --keep 10 --dry-run                # what would go: all but the 10 newest snapshots of each target
snapdelta prune --older-than 90 --outputs          # snapshots and outputs older than 90 days
snapdelta prune --target R1 --keep 3 --older-than 30
```

A snapshot is kept when one of the rules that you give keeps it: it is among the `--keep` newest of its target, or it is newer than `--older-than`. Deleting a snapshot keeps the outputs that use it; their pages then show the changed values only. The web UI has the same actions in its Admin tab, with a preview before the delete, and Delete buttons on the lists and the detail pages.

## Use a store from Python

```python
from snapdelta.output import compare
from snapdelta.store import open_store

store = open_store("sqlite:///data/snapdelta.db")  # or a directory
reference = store.save_snapshot(loaded_source)  # a Source after its load()
left = store.load_snapshot(store.resolve_snapshot("R1@latest"))
right = store.load_snapshot("R1/20261012T130856Z-before")
name = store.save_output(compare(left, right))
for target in store.targets():
    print(target, [entry.reference for entry in store.snapshot_entries(target)])
```

## Move a store to another backend

```python
from snapdelta.store import open_store

source = open_store("/data/snapdelta")  # file store
target = open_store("sqlite:///data/snapdelta.db")  # SQLite store
for name in source.targets():
    for entry in source.snapshot_entries(name):
        target.save_snapshot(source.load_snapshot(entry.reference))
for entry in source.output_entries():
    target.save_output(source.load_output(entry.name))
```

The references and names stay the same, so the outputs still find their snapshots. The plugins of the copied snapshots must be installed, because loading a snapshot needs its models.

## Add a backend

A plugin package can add a backend, for example an object store or a database server. A backend is a subclass of `snapdelta.store.Store` that implements:

- `open(address, settings)`, a class method: the store at `address`, the part of the location after `SCHEME://`; `settings` is its `[stores.SCHEME]` table, typed by `settings_class`;
- `location`: the location that opens the store again, with its scheme;
- `targets()`, `snapshot_entries()`, `output_entries()`, `info()`;
- the private primitives of the snapshots, `_has_snapshot`, `_write_snapshot`, `_read_snapshot`, `_delete_snapshot`; of the outputs, `_has_output`, `_write_output`, `_read_output`, `_replace_output`, `_delete_output`; and of Activity, `_write_run`, `_read_run`, `_run_ids` (newest first), `_delete_run`.

The base class gives the naming, the resolution of `TARGET@latest`, the checks of names, and pruning. A backend that holds connections gives them back in `close()`. To read a saved document back, `snapdelta.store.parse_snapshot()`, `parse_output()` and `parse_run()` take its JSON text or its parsed JSON and refuse a document of a schema that this snapdelta does not read; `snapshot_entry()` and `output_entry()` read a snapshot or an output entry from its name alone, as `snapshot_entries()` and `output_entries()` return them. Declare the backend under its scheme in the `pyproject.toml` of the plugin:

```toml
[project.entry-points."snapdelta.stores"]
s3 = "my_plugin.store:S3Store"
```

`--store s3://bucket/snapdelta` then opens `S3Store.open("bucket/snapdelta", settings)`.

A backend declares its settings with a subclass of `snapdelta.settings.StoreBackendSettings`, set as `settings_class`. snapdelta reads them from the table `[stores.SCHEME]`, checks them, and shows them in `snapdelta settings` and in Admin > Settings, as for the settings of a source. A secret field takes a `Secret`, so the file can name an environment variable instead of the value:

```python
from pydantic import Field
from snapdelta.settings import Secret, StoreBackendSettings
from snapdelta.store import Store


class S3Settings(StoreBackendSettings):
    region: str = "eu-west-3"
    access_key: Secret | None = None
    timeout_s: float = Field(default=10, gt=0)


class S3Store(Store):
    backend = "s3"
    settings_class = S3Settings

    @classmethod
    def open(cls, address: str, settings: StoreBackendSettings) -> "S3Store":
        chosen = settings if isinstance(settings, S3Settings) else S3Settings()
        ...
```

```toml
[store]
location = "s3://bucket/snapdelta"

[stores.s3]
access_key = { env = "S3_ACCESS_KEY" }
```

### Test a backend

`snapdelta.testing.StoreContract` holds the behaviour that every backend must have: names, `TARGET@label`, the refusal of unsafe names, outputs and reviews, pruning, Activity, and a location that opens the same store again. The file and SQLite stores pass it in the tests of snapdelta. Subclass it in the tests of the plugin, with a `store` fixture that gives a new, empty store (`pip install "snapdelta[testing]"`):

```python
import pytest
from snapdelta.testing import StoreContract

from my_plugin.store import S3Store, S3Settings


class TestS3Store(StoreContract):
    @pytest.fixture
    def store(self, bucket):
        return S3Store.open(f"{bucket}/snapdelta", S3Settings())
```

`snapdelta doctor` also checks a backend: no method of the interface left abstract, `backend` set to its scheme, and settings of the right class.

### Settings and local paths

The SQLite backend has one setting, `busy_timeout_s` in `[stores.sqlite]`: how long a writer waits for the lock of the database (default 30 seconds). The scheme alone picks the backend: the built-in schemes are `file` and `sqlite`, and a location without a scheme is a directory of the file store. When the address of a backend is a path on the machine, set `local_path = True` on its class, as `FileStore` and `SqliteStore` do: the settings file then resolves a relative address against its directory. The address of another backend stays as written.
