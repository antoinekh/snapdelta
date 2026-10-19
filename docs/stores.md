# Stores: where snapshots and diffs are saved

snapdelta saves every snapshot, every diff output and every run in a store. Two backends are built in and a third is a package of this repository; all keep the same JSON documents, so you can change the backend without losing data.

| Backend | Location | Choose it when |
|---|---|---|
| SQLite store (`SqliteStore`), the default | one SQLite file | most cases: many targets and snapshots, or several processes of one machine write at the same time |
| PostgreSQL store (`snapdelta-postgres`) | a PostgreSQL database | several servers share the store, or the database is where your team keeps its data and its backups |
| File store (`FileStore`) | a directory: one JSON file per snapshot and per output | you want to read the files, back them up with any tool, or keep them in git (private repositories only) |

> [!NOTE]
> The file store is possible, but slow for a large number of snapshots and diffs: it has no index, so a search or a filter reads each document. Use SQLite (the default) or PostgreSQL for those.

## Choose the store

The CLI and the web UI take the same option:

```bash
snapdelta --store sqlite:///data/snapdelta.db ...        # SQLite store in this file
snapdelta --store /data/snapdelta ...                    # file store in this directory
snapdelta-web --store sqlite:///data/snapdelta.db
```

Without `--store`, snapdelta uses the environment variable `SNAPDELTA_STORE`, else `[store] location` of the [settings file](settings.md), else the SQLite file `~/.local/share/snapdelta/snapdelta.db` (`$XDG_DATA_HOME/snapdelta/snapdelta.db` when `XDG_DATA_HOME` is set). Set the variable once to use the same store everywhere:

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
- `TARGET@latest` is the newest snapshot of a target, and `TARGET@LABEL` the newest with that label (`R1@before`); with a ticket, both look among the snapshots of that ticket only;
- a name alone (`20261012T130856Z-before`) works when only one target has it;
- a diff output is `<UTC time>-<left target>-vs-<right target>`: `20261012T130927Z-R1-vs-R2`; a second output of the same pair in the same second gets `.2` after the time (`20261012T130927Z.2-R1-vs-R2`), and so on.

The ticket is not in a name. Unsafe characters in a target or a label become `_`. A name with `..` or another character outside letters, digits, `_`, `.` and `-` is never found, so no name can point outside the store.

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
- The files are readable JSON. A snapshot holds `schema`, `target`, `source`, `label`, `ticket`, `captured_at`, `created_at`, `versions`, `inputs` (names and SHA-256 of the input files), `loaded` (the models), `warnings`, and `data` (the diffsync data of the load). An output holds `schema`, `created_at`, `versions`, `ticket`, `note`, `left` and `right` (target, source, label, capture time, snapshot reference), `compared`, `skipped`, `summary` and `diff` (diffsync `Diff.summary()` and `Diff.dict()`).
- Long lists read only the directory and file names: the list of targets and of the snapshots of a target never opens a file.

## SQLite store layout

```sql
create table snapshots (reference text primary key, target text not null, captured_at text not null,
                        label text, source text not null, ticket text, ticket_key text, body text not null);
create table outputs (name text primary key, created_at text not null, left_target text not null,
                      right_target text not null, ticket text, ticket_key text, body text not null);
```

- `body` is the same JSON document as the file of a file store; `ticket` repeats the ticket, and `ticket_key` the same text case-folded (`str.casefold()`), so that a search and an exact filter find it with an index.
- Indexes on the target, on the creation time and on `ticket_key` keep the lists and the ticket filters fast with many rows. The PostgreSQL store has the same columns and indexes.
- Each call opens its own connection, and a busy timeout makes a parallel writer wait instead of failing.
- You can query the file with any SQLite tool, for example `sqlite3 /data/snapdelta.db "select reference from snapshots where target = 'R1'"`.

## Formats and versions

Each document has a `schema` number. This version writes and reads schema 3, for snapshots and outputs. A store of snapdelta 0.2 must be made again.

## Clean up

The same commands work on both backends:

```bash
snapdelta info                                     # backend, location, targets, snapshots, outputs, size
snapdelta delete snapshot R1@latest R2/20261012T130856Z-before
snapdelta delete output 20261012T130927Z-R1-vs-R2
snapdelta prune --keep 10 --dry-run                # what would go: all but the 10 newest snapshots of each target
snapdelta prune --older-than 90 --outputs          # snapshots, outputs and Activity runs older than 90 days
snapdelta prune --target R1 --keep 3 --older-than 30
```

A snapshot is kept when one of the rules that you give keeps it: it is among the `--keep` newest of its target, or it is newer than `--older-than`. With `--older-than` and no `--target`, the runs of Activity older than the age go too; a run can be about many targets, so a prune of one target keeps them. Deleting a snapshot keeps the outputs that use it; their pages then show the changed values only. The web UI has the same actions in its Admin tab, with a preview before the delete, and Delete buttons on the lists and the detail pages.

## Use a store from Python

```python
from snapdelta.output import compare
from snapdelta.store import OutputQuery, SnapshotQuery, open_store

store = open_store("sqlite:///data/snapdelta.db")  # or a directory
reference = store.save_snapshot(loaded_source)  # a Source after its load()
left = store.load_snapshot(store.resolve_snapshot("R1@latest"))
right = store.load_snapshot("R1/20261012T130856Z-before")
name = store.save_output(compare(left, right))
for target in store.targets():
    print(target, [entry.reference for entry in store.snapshot_entries(SnapshotQuery(target=target))])
for entry in store.snapshot_entries(SnapshotQuery(ticket="CHG-1042", label="before")):  # every target, newest first
    print(entry.reference, entry.ticket, entry.source)
for entry in store.output_entries(OutputQuery(ticket="CHG-1042")):  # the diffs of one ticket
    print(entry.name)
```

## Move a store to another backend

```python
from snapdelta.store import open_store

source = open_store("/data/snapdelta")  # file store
target = open_store("sqlite:///data/snapdelta.db")  # SQLite store
for entry in source.snapshot_entries():
    target.save_snapshot(source.load_snapshot(entry.reference))
for entry in source.output_entries():
    target.save_output(source.load_output(entry.name))
```

The references and names stay the same, so the outputs still find their snapshots. The plugins of the copied snapshots must be installed, because loading a snapshot needs its models.

## Add a backend

A plugin package can add a backend, for example an object store or a database server. A backend is a subclass of `snapdelta.store.Store` that implements:

- `open(address, settings)`, a class method: the store at `address`, the part of the location after `SCHEME://`; `settings` is its `[stores.SCHEME]` table, typed by `settings_class`;
- `location`: the location that opens the store again, with its scheme;
- `targets()`, `snapshot_entries(query)`, `output_entries(query)`, `info()`: `snapshot_entries` takes a `SnapshotQuery(target, label, ticket)` and returns the snapshots that match every field given, of every target when `target` is None, newest first (capture time, then reference, both descending); each `SnapshotEntry` carries its `ticket` and its `source`, so that a SQL backend keeps both in columns and a list reads no body. The `label` is compared in the form of the reference (`before swap` and `before_swap` are the same), the `ticket` in any case (see `snapdelta.ticket.ticket_key()`). `output_entries` takes an `OutputQuery(search, ticket)`: `search` matches a target or a part of the ticket, `ticket` only the outputs of that ticket;
- `resolve_snapshot(reference, ticket)` is in the base class, and `describe_output(name, *, ticket, note)` sets the ticket and the note of a saved output through `_replace_output`, which must keep the ticket column;
- the private primitives of the snapshots, `_has_snapshot`, `_write_snapshot`, `_read_snapshot`, `_delete_snapshot`; of the outputs, `_has_output`, `_write_output`, `_read_output`, `_replace_output`, `_delete_output`; and of Activity, `_write_run`, `_read_run`, `_run_ids` (newest first), `_delete_run`.

The base class gives the naming, the resolution of `TARGET@latest`, the checks of names, and pruning. A backend that holds connections gives them back in `close()`. To read a saved document back, `snapdelta.store.parse_snapshot()`, `parse_output()` and `parse_run()` take its JSON text or its parsed JSON and refuse a document of a schema that this snapdelta does not read; `snapshot_entry(reference, ticket, source)` and `output_entry()` build an entry from its name, and `snapshot_row(reference, snapshot)` gives the entry to keep in the columns of a snapshot being saved, as `snapshot_entries()` and `output_entries()` return them. Declare the backend under its scheme in the `pyproject.toml` of the plugin:

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

`snapdelta.testing.StoreContract` holds the behaviour that every backend must have: names, `TARGET@label`, the refusal of unsafe names, outputs and reviews, the ticket of snapshots and outputs, pruning, Activity, and a location that opens the same store again. The file and SQLite stores pass it in the tests of snapdelta. Subclass it in the tests of the plugin, with a `store` fixture that gives a new, empty store (`pip install "snapdelta[testing]"`):

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
