"""The store `postgresql://`: the tables of the SQLite store in a PostgreSQL database, with the bodies as json.

json and not jsonb: jsonb sorts the keys, and the order of the models and objects of a diff is the order to show.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, LiteralString

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool, PoolTimeout
from pydantic import Field

from snapdelta.activity import Run
from snapdelta.errors import NotFoundError, SnapdeltaError
from snapdelta.output import Output
from snapdelta.settings import Secret, StoreBackendSettings
from snapdelta.snapshot import SnapshotFile
from snapdelta.store import (
    RUN_ID_RE,
    OutputEntry,
    OutputQuery,
    SnapshotEntry,
    SnapshotQuery,
    Store,
    StoreInfo,
    output_entry,
    parse_output,
    parse_run,
    parse_snapshot,
    refuse_older_labels,
    refuse_older_tables,
    snapshot_conditions,
    snapshot_entry,
    snapshot_row,
)
from snapdelta.ticket import ticket_key

SCHEME = "postgresql"
TABLES = """
create table if not exists snapdelta_snapshots (
    reference text primary key,
    target text not null,
    captured_at timestamptz not null,
    label text,
    source text not null,
    ticket text,
    ticket_key text,
    body json not null
);
create index if not exists snapdelta_snapshots_by_target on snapdelta_snapshots (target, reference);
create index if not exists snapdelta_snapshots_by_ticket on snapdelta_snapshots (ticket_key);
create index if not exists snapdelta_snapshots_by_label on snapdelta_snapshots (label);
create index if not exists snapdelta_snapshots_by_time
    on snapdelta_snapshots (captured_at desc, reference collate "C" desc);
create table if not exists snapdelta_outputs (
    name text primary key,
    created_at timestamptz not null,
    left_target text not null,
    right_target text not null,
    ticket text,
    ticket_key text,
    body json not null
);
create index if not exists snapdelta_outputs_by_time on snapdelta_outputs (created_at);
create index if not exists snapdelta_outputs_by_ticket on snapdelta_outputs (ticket_key);
create table if not exists snapdelta_runs (
    id text primary key,
    body json not null
);
"""
# Two servers that start at the same time must not create the tables twice.
LOCK_ID = 0x736E6170


class PostgresSettings(StoreBackendSettings):
    password: Secret | None = Field(
        default=None,
        description="The password of the database user; never in the location, which the pages and the runs show.",
    )
    connect_timeout_s: float = Field(default=10, gt=0, description="How long a connection may take, in seconds.")
    pool_size: int = Field(
        default=4, ge=1, le=100, description="The connections that the store keeps open, for the requests at once."
    )


class PostgresStore(Store):
    """`postgresql://USER@HOST:PORT/DATABASE?OPTIONS`: any libpq URI, without its password."""

    backend = SCHEME
    settings_class = PostgresSettings

    def __init__(self, address: str, settings: PostgresSettings) -> None:
        conninfo = f"{SCHEME}://{address}"
        try:
            parts = conninfo_to_dict(conninfo)
        except psycopg.ProgrammingError as error:
            raise SnapdeltaError(f"{SCHEME}://{_without_password(address)}: not a valid location ({error})") from error
        if "password" in parts:
            raise SnapdeltaError(
                "the location of a postgresql store holds a password, and snapdelta shows the location: "
                "put the password in [stores.postgresql] password, or in PGPASSWORD or ~/.pgpass"
            )
        self.address = address
        connection: dict[str, Any] = {"connect_timeout": math.ceil(settings.connect_timeout_s)}
        if settings.password is not None:
            connection["password"] = settings.password.value()
        self.pool = ConnectionPool(conninfo, kwargs=connection, min_size=1, max_size=settings.pool_size, open=False)
        try:
            self.pool.open(wait=True, timeout=settings.connect_timeout_s)
        except PoolTimeout as error:
            self.pool.close()
            raise SnapdeltaError(f"{self.location}: no connection within {settings.connect_timeout_s} s") from error
        try:
            with self._connect() as db:
                db.execute("select pg_advisory_xact_lock(%s)", (LOCK_ID,))
                for table in ("snapdelta_snapshots", "snapdelta_outputs"):
                    columns = db.execute(
                        "select column_name from information_schema.columns "
                        "where table_schema = current_schema() and table_name = %s",
                        (table,),
                    ).fetchall()
                    refuse_older_tables([column for (column,) in columns])
                db.execute(TABLES)
                refuse_older_labels(db.execute("select reference, label from snapdelta_snapshots limit 1"))
        except SnapdeltaError:
            self.pool.close()
            raise
        except psycopg.Error as error:
            self.pool.close()
            raise SnapdeltaError(f"{self.location}: {error}") from error

    @classmethod
    def open(cls, address: str, settings: StoreBackendSettings) -> PostgresStore:
        return cls(address, settings if isinstance(settings, PostgresSettings) else PostgresSettings())

    @property
    def location(self) -> str:
        return f"{SCHEME}://{self.address}"

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        """A connection of the pool, in one transaction that commits at the end of the block."""
        with self.pool.connection() as db:
            yield db

    def targets(self, query: str = "") -> list[str]:
        wanted = query.strip().lower()
        with self._connect() as db:
            rows = db.execute(
                "select target from snapdelta_snapshots where strpos(lower(target), %s) > 0 "
                'group by target order by target collate "C"',
                (wanted,),
            ).fetchall()
        return [row[0] for row in rows]

    def snapshot_entries(self, query: SnapshotQuery = SnapshotQuery()) -> list[SnapshotEntry]:
        conditions = snapshot_conditions(query)
        filters = [sql.SQL("{} = any(%s)").format(sql.Identifier(column)) for column, _ in conditions]
        where = sql.SQL(" where {}").format(sql.SQL(" and ").join(filters)) if filters else sql.SQL("")
        statement = sql.SQL(
            "select reference, ticket, source from snapdelta_snapshots{} "
            'order by captured_at desc, reference collate "C" desc limit %s offset %s'
        ).format(where)
        # PostgreSQL reads a limit of NULL as no limit.
        values = [list(wanted) for _, wanted in conditions] + [query.limit, query.offset]
        with self._connect() as db:
            rows = db.execute(statement, values).fetchall()
        return [entry for reference, ticket, source in rows if (entry := snapshot_entry(reference, ticket, source))]

    def output_entries(self, query: OutputQuery = OutputQuery()) -> list[OutputEntry]:
        wanted = ticket_key(query.search) or ""
        key = ticket_key(query.ticket)
        with self._connect() as db:
            rows = db.execute(
                "select name from snapdelta_outputs "
                "where (strpos(lower(left_target), %s) > 0 or strpos(lower(right_target), %s) > 0 "
                "or strpos(coalesce(ticket_key, ''), %s) > 0) and (%s::text is null or ticket_key = %s) "
                'order by name collate "C" desc',
                (wanted, wanted, wanted, key, key),
            ).fetchall()
        return [entry for (name,) in rows if (entry := output_entry(name))]

    def info(self) -> StoreInfo:
        with self._connect() as db:
            row = db.execute(
                "select (select count(distinct target) from snapdelta_snapshots), "
                "(select count(*) from snapdelta_snapshots), (select count(*) from snapdelta_outputs), "
                "(select count(*) from snapdelta_runs), "
                "pg_total_relation_size('snapdelta_snapshots') + pg_total_relation_size('snapdelta_outputs') "
                "+ pg_total_relation_size('snapdelta_runs')"
            ).fetchone()
        assert row is not None
        targets, snapshots, outputs, runs, size = row
        return StoreInfo(self.backend, self.location, targets, snapshots, outputs, size, runs)

    def _has_snapshot(self, reference: str) -> bool:
        with self._connect() as db:
            found = db.execute("select 1 from snapdelta_snapshots where reference = %s", (reference,)).fetchone()
        return found is not None

    def _write_snapshot(self, reference: str, snapshot: SnapshotFile) -> None:
        row = snapshot_row(reference, snapshot)
        # The moment and the label of the reference, so that every store orders and filters the same way.
        values = (
            reference,
            row.target,
            row.captured_at,
            row.label,
            row.source,
            row.ticket,
            ticket_key(row.ticket),
            Json(snapshot.model_dump(mode="json", by_alias=True)),
        )
        try:
            with self._connect() as db:
                db.execute(
                    "insert into snapdelta_snapshots "
                    "(reference, target, captured_at, label, source, ticket, ticket_key, body) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s)",
                    values,
                )
        except psycopg.errors.UniqueViolation as error:
            # Another writer saved the same reference between the check and this insert.
            raise SnapdeltaError(f"snapshot {reference} already exists: give another label") from error

    def _read_snapshot(self, reference: str) -> SnapshotFile:
        body = self._body("select body from snapdelta_snapshots where reference = %s", reference, "snapshot")
        return parse_snapshot(body, reference)

    def _delete_snapshot(self, reference: str) -> None:
        with self._connect() as db:
            db.execute("delete from snapdelta_snapshots where reference = %s", (reference,))

    def _has_output(self, name: str) -> bool:
        with self._connect() as db:
            return db.execute("select 1 from snapdelta_outputs where name = %s", (name,)).fetchone() is not None

    def _write_output(self, name: str, output: Output) -> None:
        values = (
            name,
            output.created_at,
            output.left.target,
            output.right.target,
            output.ticket,
            ticket_key(output.ticket),
            Json(output.model_dump(mode="json", by_alias=True)),
        )
        with self._connect() as db:
            db.execute(
                "insert into snapdelta_outputs (name, created_at, left_target, right_target, ticket, ticket_key, body) "
                "values (%s, %s, %s, %s, %s, %s, %s)",
                values,
            )

    def _read_output(self, name: str) -> Output:
        return parse_output(self._body("select body from snapdelta_outputs where name = %s", name, "output"), name)

    def _replace_output(self, name: str, output: Output) -> None:
        with self._connect() as db:
            db.execute(
                "update snapdelta_outputs set ticket = %s, ticket_key = %s, body = %s where name = %s",
                (output.ticket, ticket_key(output.ticket), Json(output.model_dump(mode="json", by_alias=True)), name),
            )

    def _delete_output(self, name: str) -> None:
        with self._connect() as db:
            db.execute("delete from snapdelta_outputs where name = %s", (name,))

    def _write_run(self, run: Run) -> None:
        with self._connect() as db:
            db.execute(
                "insert into snapdelta_runs (id, body) values (%s, %s) "
                "on conflict (id) do update set body = excluded.body",
                (run.id, Json(run.model_dump(mode="json"))),
            )

    def _read_run(self, run_id: str) -> Run:
        return parse_run(self._body("select body from snapdelta_runs where id = %s", run_id, "run"), run_id)

    def _run_ids(self) -> list[str]:
        with self._connect() as db:
            rows = db.execute('select id from snapdelta_runs order by id collate "C" desc').fetchall()
        return [row[0] for row in rows if RUN_ID_RE.match(row[0])]

    def _delete_run(self, run_id: str) -> None:
        with self._connect() as db:
            db.execute("delete from snapdelta_runs where id = %s", (run_id,))

    def _body(self, query: LiteralString, key: str, kind: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(query, (key,)).fetchone()
        # Another process can delete the row between the existence check and this read.
        if row is None:
            raise NotFoundError(f"no {kind} {key!r} in {self.location}")
        return row[0]


def _without_password(address: str) -> str:
    user, at, rest = address.rpartition("@")
    return f"{user.partition(':')[0]}{at}{rest}" if at else address
