# Changelog

## Unreleased

### Added

- A diff has a change: any reference of the work that it belongs to (`CHG-1042`, `#216`, a Jira key), apart from its title; the text and Markdown formats show it.

### Changed

- `Store.output_entries(query)` also matches the change of a diff, and `describe_output()` takes the change; a store backend of a plugin must do the same, which `StoreContract` checks. SQLite and PostgreSQL get a `change` column: make again a store of 0.2.
- The diff list shows a **Change** column, and its filter finds diffs by target or change; the diff page shows the change next to the title, and its edit form, **New diff** and **Many targets** have a **Change** field.

### Fixed

- On the page of a diff, the description is under the title also when the title is short.

### Removed

- The diff list no longer groups the diffs by the start of their title before `: `; give them a change.

## v0.2.0

### Added

- `snapdelta doctor` checks each plugin against its interface (a source with no `load()` or a child model that is not one of its models, a store backend with methods left abstract, a format that fails on a sample diff, settings classes of the wrong kind), then the settings, the store, the inventory and the models that two sources name the same way; exit code 1 on an error, `--json` for a pipeline.
- `snapdelta.testing` (extra `snapdelta[testing]`): `StoreContract` and `SourceContract`, the tests that every store backend and every source must pass, for the tests of a plugin; the built-in stores and the example sources pass them.
- Secret backends, a fifth entry point group, `snapdelta.secrets`: a secret is a plain value or `{ BACKEND = "NAME" }`, with the built-in backends `env` and `file` (`{ file = "/run/secrets/token" }`, for Docker and Kubernetes secrets), and a plugin backend such as a vault, with its settings in `[secrets.NAME]`. The web form chooses the backend of a secret.
- Notifiers, a sixth entry point group, `snapdelta.notifiers`: after each snapshot and saved diff, each `[notifiers.NAME]` table whose filter matches (`on` failure, differences or success, `kinds`, inventory `groups`) gets the run. The built-in `webhook` posts JSON with a chat line (Slack, Teams, Mattermost) and `command` gives the JSON to a program; the notifiers send at the same time, a run records `sent` or the error of each notifier, and a notifier that fails never fails the run. `[web] public_url` gives the messages a link to the diff or the run.
- Compare rules, a seventh entry point group, `snapdelta.rules`: `[profiles.NAME] rules = { "interface.description" = "case-insensitive" }` accepts two values that mean the same. Built in: `case-insensitive`, `whitespace`, `unordered` and `ip-address` (`2001:db8::1` and `2001:DB8:0:0::1`).
- `snapdelta plugins new NAME --kind KIND`: a plugin package to start from, with one working plugin of each kind (source, store, format, inventory, secret, notifier, rule), its entry point and its tests, which pass at once; the store is an in-memory backend that passes `StoreContract`.
- Each CLI command, `snapdelta doctor`, the web server and the MCP server close the store that they opened.
- For a store backend of a plugin: `snapdelta.store.snapshot_entry()`, `output_entry()`, and `parse_snapshot()`, `parse_output()`, `parse_run()`, which check a saved document and its schema version; `Store.close()` gives back the connections of a backend, and the web server calls it on a store that the settings replace.
- Runners, an eighth entry point group, `snapdelta.runners`: `[snapshot] runner` chooses how the loads of a snapshot run, `threads` (the default) or `processes` (one process per load, for a plugin that parses much or is not thread-safe), or a runner of a plugin such as a queue. A load is data, `LoadTask`, and `execute()` runs it in any process.
- Auth backends of the web UI, the API and MCP, the entry point group `snapdelta.auth` of `snapdelta-web`: `[web] auth` is `token`, `header` (the user that a trusted reverse proxy gives in a header, for SSO, with `[auth.header] trusted_proxies`) or `none`, or a backend of a plugin such as LDAP or OIDC, with its login fields and its `[auth.NAME]` table. Each run records the user of its request, and an accepted change takes the user that the server knows; the token login can give a name.
- A package declares its own extension points in the group `snapdelta.extension_points`, as `snapdelta-web` does for `snapdelta.auth`; `snapdelta plugins`, `snapdelta doctor` and **Admin > Plugins** list them with the ones of the core.
- Models by inventory group: a top-level model with `_groups = ("edge",)` is loaded by the hosts of these groups only; a source gets the models of its host in `self.wanted` and `self.wants(MODEL)` before its load, the snapshot records them, and `snapdelta models`, **Admin > Models** and `snapdelta doctor` show and check the groups.
- A diff of many targets at once, for a change on many devices: `snapdelta diff --group core @before @after` (or `--target`, `--all-targets`, and `--live`, which takes the after snapshots of every target at the same time through the runner) compares `TARGET@before` with `TARGET@after` for each target, prints each diff then the targets that changed, did not change or failed, and exits with `2` on a failure, else `1` on a difference. The same in **New diff > Many targets** of the web UI, `POST /api/v1/diffs/targets` and the MCP tool `compare_targets`, with one run in Activity. A format gives several outputs at once with `Renderer.render_many` (`json`: one list).
- Expected changes: `snapdelta diff --expect FILE` (and `expected` in the API and the MCP tools) checks a diff against what the change must do; the output keeps the expected changes done, the missing ones and the unexpected changes, the formats and the diff page show them, and the exit code is `0` only when the diff is the expected changes.
- A container image, `ghcr.io/antoinekh/snapdelta`, published at each release: `snapdelta-web` with the PostgreSQL store, the settings in `/etc/snapdelta/snapdelta.toml`, the store on the volume `/data`, a health check, and plugins added in a derived image; `docker/compose.yaml` runs it with PostgreSQL. `GET /healthz` answers with no login.
- `snapdelta plugins`, **Admin > Plugins** and `GET /api/v1/plugins` list the plugins of every extension point, the built-ins and the broken ones included, with their package, version and problems, and where the settings use each one (the store, the runner, a secret, a notifier, a profile); each extension point says it with `in_use`.
- `snapdelta-postgres`, a third package: the PostgreSQL store backend, `--store postgresql://USER@HOST/DATABASE`, with its password in `[stores.postgresql]` (any secret backend), a pool of connections, and the documents as `json`; it passes `StoreContract` against a server in CI and with `make test-postgres`.
- `docs/plugins.md`: the plug-in architecture, each extension point, how to write, check and test a plugin, and how to add an extension point; the README says it on its front page.

### Changed

- A snapshot has no `unmapped` list any more, nor the web UI and `snapdelta snapshot` its count: no source filled it. snapdelta still reads the snapshots that hold one; snapdelta 0.1 cannot read the snapshots that this version saves.
- A run lists its diffs in `outputs`, in place of `output` (the runs saved by 0.1 still read); `--target` of `snapdelta diff` now selects targets, and `--live` fetches the target of the left snapshot.
- `snapdelta diff` has no `--source` and `--file` any more: take a snapshot of the files (`snapdelta snapshot FILES --source S --label after`), then compare two snapshots, the one way to give the right side. `diff_snapshots(right=SOURCE)` stays in Python.
- One plugin registry, `snapdelta.plugins`, for every entry point group, and the built-in stores, formats and Nornir inventory are entry points of the `snapdelta` package. A store backend, a format or an inventory format that cannot load is left out with its error, as a source already was, instead of a traceback; so is a plugin of the wrong kind, a name that two packages give, and a source whose `source` is not its entry point name.
- `snapdelta.settings.Auth` is gone: `[web] auth` names an auth backend. The functions `token_of()`, `access_text()` and `valid_session()` of `snapdelta_web.auth` are now the `TokenAuth` backend, `describe()` and `session_user()`; a session cookie also holds the user name, so the sessions of a server before this version end.
- `Secret(env="NAME")` is `Secret(backend="env", reference="NAME")` in Python; the file keeps `{ env = "NAME" }`.
- `take_snapshots()` and `snapshot_targets()` give each saved snapshot as a `Taken` of data (`reference`, `loaded`, `warnings`) in place of the loaded source, and refuse a source class that is not the one of the registry; `Progress` moves to `snapdelta.runners`.
- The package of a plugin comes from its entry point: `Source.distribution` is gone, `register_source(cls)` takes the class only, and in `GET /api/v1/version` each plugin package lists `plugins` (`source example`) in place of `sources`.
- **New diff** has a switch, **One target** or **Many targets**, with the same Before and After boxes, fields and **Compare** button; the After side takes the target of the Before side until you choose another one, and Many targets also takes a description.
- **Admin > Plugins** shows a green check mark for a plugin in use; its title lists where the settings use it.
- The page of a target has a **From** and **To** filter (UTC) for its timeline and its table of snapshots; the timeline fades the edge that holds more snapshots.
- The diff page has no **Your name** field when a login gives the user.
- **Take snapshot** has the style of **New diff > Many targets**: one **Targets** box, then the label and the button.
- **Diffs** and the page of a target show a bar fixed at the bottom of the screen when rows are ticked, with their count, **Clear** and **Delete**, in place of a button under the list; a delete on the page of a target keeps its period.
- The bar of the page of a target has **Compare**: two ticked snapshots open their diff, the older one on the left.
- **Admin > Settings** has links to its sections, and a menu for a field with known values: `log_level`, and the installed runners, auth backends and inventory formats.
- **Activity** filters the runs by result and by period, and its rows are shorter.
- The screenshots of the docs show the web UI of this version, with one more for the page of a target.

### Fixed

- A label next to its field (**From**, **To**, **Result**, **Profile**, **Your name**) has a space before the field.
- A command of the lab that fails in its container gives its exit code and its reason, which `docker exec` writes on stdout when the program is missing; the error was empty.
- In **Admin > Settings**, the new value of a secret has the style of the other fields.
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
