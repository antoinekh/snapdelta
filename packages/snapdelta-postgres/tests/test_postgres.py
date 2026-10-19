"""The PostgreSQL store against a server: set SNAPDELTA_POSTGRES_URL, such as `make test-postgres` does."""

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from snapdelta.errors import SnapdeltaError
from snapdelta.settings import Secret
from snapdelta.store import open_store
from snapdelta.testing import StoreContract, sample
from snapdelta_postgres import PostgresSettings, PostgresStore

# An administrator of a server, which may create and drop a database for each test.
SERVER = os.environ.get("SNAPDELTA_POSTGRES_URL", "")
needs_server = pytest.mark.skipif(not SERVER, reason="set SNAPDELTA_POSTGRES_URL to test against PostgreSQL")


@pytest.fixture
def database() -> Iterator[str]:
    """The address of a new, empty database, dropped after the test."""
    name = f"snapdelta_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(SERVER, autocommit=True) as admin:
        admin.execute(f'create database "{name}"')
    yield SERVER.removeprefix("postgresql://").rsplit("/", 1)[0] + f"/{name}"
    with psycopg.connect(SERVER, autocommit=True) as admin:
        admin.execute(f'drop database "{name}" with (force)')


@needs_server
class TestPostgresStore(StoreContract):
    @pytest.fixture
    def store(self, database: str) -> Iterator[PostgresStore]:
        store = PostgresStore.open(database, PostgresSettings())
        yield store
        store.close()


@needs_server
def test_the_scheme_opens_the_backend_and_two_stores_share_the_tables(database: str) -> None:
    first = open_store(f"postgresql://{database}")
    second = open_store(f"postgresql://{database}")
    try:
        assert isinstance(first, PostgresStore) and first.info().backend == "postgresql"
        reference = first.save_snapshot(sample())
        assert second.resolve_snapshot("R1@latest") == reference
        with pytest.raises(SnapdeltaError, match="already exists"):
            second.save_snapshot(sample())
    finally:
        first.close()
        second.close()


@needs_server
def test_a_store_of_an_older_snapdelta_is_refused(database: str) -> None:
    with psycopg.connect(f"postgresql://{database}") as db:
        db.execute("create table snapdelta_snapshots (reference text primary key, target text not null)")
    with pytest.raises(SnapdeltaError, match="made by an older snapdelta: make it again"):
        PostgresStore.open(database, PostgresSettings())


@needs_server
def test_a_password_of_the_settings_is_used(database: str, monkeypatch: pytest.MonkeyPatch) -> None:
    password = os.environ.get("PGPASSWORD")
    if password is None:
        pytest.skip("the test server needs no password")
    monkeypatch.delenv("PGPASSWORD")
    store = PostgresStore.open(database, PostgresSettings(password=Secret(plain=password)))
    store.close()


def test_a_location_with_a_password_is_refused() -> None:
    with pytest.raises(SnapdeltaError, match=r"put the password in \[stores.postgresql\] password"):
        PostgresStore.open("snapdelta:s3cret@db.example.net/snapdelta", PostgresSettings())


def test_a_server_that_does_not_answer_is_an_error_message() -> None:
    with pytest.raises(SnapdeltaError, match="no connection within 1.0 s"):
        PostgresStore.open("snapdelta@127.0.0.1:9/snapdelta", PostgresSettings(connect_timeout_s=1))
