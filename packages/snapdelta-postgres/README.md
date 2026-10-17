# snapdelta-postgres

A PostgreSQL store backend for [snapdelta](https://github.com/antoinekh/snapdelta): the snapshots, the diffs and the runs in a database, which many snapdelta servers and workers can share.

```bash
pip install snapdelta-postgres
snapdelta --store postgresql://snapdelta@db.example.net/snapdelta info
```

The location is a libpq URI without its password, such as `postgresql://USER@HOST:PORT/DATABASE?sslmode=require`; snapdelta shows the location in its pages and its runs. Give the password in the settings, or in `PGPASSWORD` or `~/.pgpass`:

```toml
[store]
location = "postgresql://snapdelta@db.example.net/snapdelta"

[stores.postgresql]
password = { env = "SNAPDELTA_DB_PASSWORD" }
connect_timeout_s = 10
pool_size = 4
```

The store creates its three tables, `snapdelta_snapshots`, `snapdelta_outputs` and `snapdelta_runs`, at its first use; the user needs the right to create tables in the database. The bodies are the JSON documents of the file store, as `jsonb`. See [docs/stores.md](https://github.com/antoinekh/snapdelta/blob/main/docs/stores.md).
