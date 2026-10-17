# Changelog

## Unreleased

### Added

- `snapdelta doctor` checks each plugin against its interface (a source with no `load()` or a child model that is not one of its models, a store backend with methods left abstract, a format that fails on a sample diff, settings classes of the wrong kind), then the settings, the store, the inventory and the models that two sources name the same way; exit code 1 on an error, `--json` for a pipeline.
- `snapdelta.testing` (extra `snapdelta[testing]`): `StoreContract` and `SourceContract`, the tests that every store backend and every source must pass, for the tests of a plugin; the built-in stores and the example sources pass them.
- Secret backends, a fifth entry point group, `snapdelta.secrets`: a secret is a plain value or `{ BACKEND = "NAME" }`, with the built-in backends `env` and `file` (`{ file = "/run/secrets/token" }`, for Docker and Kubernetes secrets), and a plugin backend such as a vault, with its settings in `[secrets.NAME]`. The web form chooses the backend of a secret.
- `snapdelta plugins`, **Admin > Plugins** and `GET /api/v1/plugins` list the plugins of every extension point, the built-ins and the broken ones included, with their package, version and problems.

### Changed

- One plugin registry, `snapdelta.plugins`, for the four entry point groups, and the built-in stores, formats and Nornir inventory are entry points of the `snapdelta` package. A store backend, a format or an inventory format that cannot load is left out with its error, as a source already was, instead of a traceback; so is a plugin of the wrong kind, a name that two packages give, and a source whose `source` is not its entry point name.
- `Secret(env="NAME")` is `Secret(backend="env", reference="NAME")` in Python; the file keeps `{ env = "NAME" }`.
- The package of a plugin comes from its entry point: `Source.distribution` is gone, `register_source(cls)` takes the class only, and in `GET /api/v1/version` each plugin package lists `plugins` (`source example`) in place of `sources`.

### Fixed

- `snapdelta prune` and the admin page refuse a negative keep or age, which used to delete every snapshot.
- `snapdelta prune` and the admin page refuse an age too big for a date, which used to raise `OverflowError` (a traceback in the CLI, a 500 page in the web UI).
- MCP tools refuse a page out of range: `offset` below 0, and `limit` below 1 or above 500.
- A malformed Nornir inventory reports which entry is wrong and why (a host that is not a mapping, a port that is not a number, groups that are not a list) instead of a `TypeError`, `AttributeError` or `ValueError` traceback.
- `~` in `--settings`, `SNAPDELTA_SETTINGS`, `--store`, `SNAPDELTA_STORE` and the web `--tls-cert`/`--tls-key` means the home directory, as it already did inside a settings file.

## v0.1.0

### Added

- First version: snapshot any data through plugin sources (diffsync models), from input files or fetched from targets, and compare two snapshots at two moments or between two targets; fields kept but never compared.
- Compare profiles (ignore, tolerance, counts, pairs), a live compare, outputs in text, JSON, Markdown and CSV, titles and descriptions, and review: accept each change with a name and a comment.
- File and SQLite stores, an optional Nornir inventory, parallel loads, one `snapdelta.toml` settings file, and Activity: each snapshot and diff is a run with the log of its loads.
- CLI `snapdelta`, and web UI `snapdelta-web`: diffs with a change map and inline marks, a timeline per target, snapshots that run in the background with live progress, a command palette and keyboard shortcuts; no internet access needed.
- JSON API and MCP server for pipelines and AI agents (token login or none, network access, TLS), with `/api/v1/docs` and `/mcp/docs`, and a skill for agents that use the CLI.
- Plugin entry points for sources, stores, formats and inventories, with a settings table for each source (`[sources.NAME]`), store backend (`[stores.SCHEME]`) and inventory format (`[inventories.NAME]`); an example plugin, a lab with FRR and Nokia SR Linux, and guides.
