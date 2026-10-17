# Changelog

## Unreleased

### Added

- `snapdelta doctor` checks each plugin against its interface (a source with no `load()` or a child model that is not one of its models, a store backend with methods left abstract, a format that fails on a sample diff, settings classes of the wrong kind), then the settings, the store, the inventory and the models that two sources name the same way; exit code 1 on an error, `--json` for a pipeline.
- `snapdelta.testing` (extra `snapdelta[testing]`): `StoreContract` and `SourceContract`, the tests that every store backend and every source must pass, for the tests of a plugin; the built-in stores and the example sources pass them.
- Secret backends, a fifth entry point group, `snapdelta.secrets`: a secret is a plain value or `{ BACKEND = "NAME" }`, with the built-in backends `env` and `file` (`{ file = "/run/secrets/token" }`, for Docker and Kubernetes secrets), and a plugin backend such as a vault, with its settings in `[secrets.NAME]`. The web form chooses the backend of a secret.
- Notifiers, a sixth entry point group, `snapdelta.notifiers`: after each snapshot and saved diff, each `[notifiers.NAME]` table whose filter matches (`on` failure, differences or success, `kinds`, inventory `groups`) gets the run. The built-in `webhook` posts JSON with a chat line (Slack, Teams, Mattermost) and `command` gives the JSON to a program; a run records `sent` or the error of each notifier, and a notifier that fails never fails the run. `[web] public_url` gives the messages a link to the diff or the run.
- Compare rules, a seventh entry point group, `snapdelta.rules`: `[profiles.NAME] rules = { "interface.description" = "case-insensitive" }` accepts two values that mean the same. Built in: `case-insensitive`, `whitespace`, `unordered` and `ip-address` (`2001:db8::1` and `2001:DB8:0:0::1`).
- `snapdelta plugins new NAME --kind KIND`: a plugin package to start from, with one working plugin of each kind (source, store, format, inventory, secret, notifier, rule), its entry point and its tests, which pass at once; the store is an in-memory backend that passes `StoreContract`.
- Each CLI command, `snapdelta doctor`, the web server and the MCP server close the store that they opened.
- For a store backend of a plugin: `snapdelta.store.snapshot_entry()`, `output_entry()`, and `parse_snapshot()`, `parse_output()`, `parse_run()`, which check a saved document and its schema version; `Store.close()` gives back the connections of a backend, and the web server calls it on a store that the settings replace.
- Runners, an eighth entry point group, `snapdelta.runners`: `[snapshot] runner` chooses how the loads of a snapshot run, `threads` (the default) or `processes` (one process per load, for a plugin that parses much or is not thread-safe), or a runner of a plugin such as a queue. A load is data, `LoadTask`, and `execute()` runs it in any process.
- Auth backends of the web UI, the API and MCP, the entry point group `snapdelta.auth` of `snapdelta-web`: `[web] auth` is `token`, `header` (the user that a trusted reverse proxy gives in a header, for SSO, with `[auth.header] trusted_proxies`) or `none`, or a backend of a plugin such as LDAP or OIDC, with its login fields and its `[auth.NAME]` table. Each run records the user of its request, and an accepted change takes the user that the server knows; the token login can give a name.
- A package declares its own extension points in the group `snapdelta.extension_points`, as `snapdelta-web` does for `snapdelta.auth`; `snapdelta plugins`, `snapdelta doctor` and **Admin > Plugins** list them with the ones of the core.
- `snapdelta plugins`, **Admin > Plugins** and `GET /api/v1/plugins` list the plugins of every extension point, the built-ins and the broken ones included, with their package, version and problems.

- `snapdelta-postgres`, a third package: the PostgreSQL store backend, `--store postgresql://USER@HOST/DATABASE`, with its password in `[stores.postgresql]` (any secret backend), a pool of connections, and the documents as `jsonb`; it passes `StoreContract` against a server in CI and with `make test-postgres`.
- `docs/plugins.md`: the plug-in architecture, each extension point, how to write, check and test a plugin, and how to add an extension point; the README says it on its front page.

### Changed

- One plugin registry, `snapdelta.plugins`, for the four entry point groups, and the built-in stores, formats and Nornir inventory are entry points of the `snapdelta` package. A store backend, a format or an inventory format that cannot load is left out with its error, as a source already was, instead of a traceback; so is a plugin of the wrong kind, a name that two packages give, and a source whose `source` is not its entry point name.
- `snapdelta.settings.Auth` is gone: `[web] auth` names an auth backend. The functions `token_of()`, `access_text()` and `valid_session()` of `snapdelta_web.auth` are now the `TokenAuth` backend, `describe()` and `session_user()`; a session cookie also holds the user name, so the sessions of a server before this version end.
- `Secret(env="NAME")` is `Secret(backend="env", reference="NAME")` in Python; the file keeps `{ env = "NAME" }`.
- `take_snapshots()` and `snapshot_targets()` give each saved snapshot as a `Taken` of data (`reference`, `loaded`, `warnings`, `unmapped`) in place of the loaded source, and refuse a source class that is not the one of the registry; `Progress` moves to `snapdelta.runners`.
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
