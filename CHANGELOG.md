# Changelog

## v0.3.0

### Added

- A snapshot and a diff have a ticket: any text that names the work (`CHG-1042`), 80 characters at most, one line.
- The ticket is set by `--ticket` in the CLI, by `ticket` in the API and in MCP, and by the **Ticket** field of the web UI.
- With a ticket, `TARGET@LABEL` and `TARGET@latest` name the snapshots of that ticket only, so `before` and `after` never mix.
- `TARGET/NAME` is not scoped by a ticket.
- A diff with no ticket takes the ticket of its two snapshots when they have the same one.
- A filter by ticket: `snapdelta list --ticket`, `?ticket=` in the API and in the web UI, and `ticket` in the MCP tools.
- `snapdelta list --ticket` filters the snapshots too, not only the outputs.
- The search (`snapdelta list --target`, `q`, `query`) also finds a part of a ticket.
- The snapshot lists find the snapshots of every target by label and by ticket, and each row shows its `ticket` and its `source`.
- The API takes a repeated `target`, and MCP takes `targets`, a list. Neither needs a target, and an empty filter does not filter.
- `GET /api/v1/snapshots` has a `limit` (50 by default, 500 at most), and the store reads only that page.
- The page of a snapshot has an **Export** menu: **json** downloads the document, **API call** copies the `curl` command.
- The ticket of a snapshot shows in its title as a chip that lists the snapshots of that ticket.
- The prompt `check_change` of MCP takes an optional `ticket`, which it gives to the snapshots and the diffs.
- The error says when a label exists in another ticket, and what to do.
- The text format shows the line `ticket: X`, and the Markdown format the heading `# TICKET · LEFT vs RIGHT`.
- A ticket or a note that is too long is refused before any target is fetched.
- A prune by age (`snapdelta prune --older-than`) also deletes the runs of Activity older than the age, when it prunes every target. `PrunePlan` has `runs`.
- A **Discard** button next to **Save** in the Settings and Profiles forms restores the saved values.

### Changed

- SQLite is the default store: `~/.local/share/snapdelta/snapdelta.db`, or `$XDG_DATA_HOME/snapdelta/snapdelta.db`.
- The old default directory is not read any more. Keep it with `--store`, `SNAPDELTA_STORE` or `[store] location`.
- After a move to SQLite, delete only `snapshots/`, `outputs/` and `runs/` of the old store.
- The file store stays a backend, documented as slow for a large number of snapshots and diffs.
- A diff has a note, free text on many lines (2000 characters at most), in place of its title and its description.
- `--note` replaces `--title` and `--description`, and so do `note` in the API, in MCP and in the web UI.
- The lists show the first line of the note, cut at 120 characters.
- The API refuses an argument it does not know, and MCP refuses the old argument `target` with a message that names `targets`.
- In **Admin > Profiles**, each profile has a **Delete** button, with a confirmation, in place of the **Remove profile** checkbox.
- A profile is created from its **Name** and a **Create** button, in place of the group of nine fields.
- **Admin > Profiles** no longer shows the `file` or `default` tag at the end of each field. **Admin > Settings** keeps it.
- The groups of **Admin > Settings** and **Admin > Profiles** have the box and the title size of the other Admin panels.
- **Create** is a secondary button, so **Save profiles** is the only primary action of the page.
- The save bar shows only the change status, **Discard** and **Save**.
- **Admin > Store** says **Diffs** in place of **Outputs**, and **Keep newest, per target** in place of **Keep newest**.
- On **Admin > Inventory**, the hosts table has its **Hosts** panel, like **Groups**.
- The diff list has a **Ticket** column, and its filter finds the diffs by target or by a part of the ticket.
- `snapdelta list` writes `ticket: X` and `source: Y` on a snapshot line.
- Snapshots and outputs are schema 3. `Store.resolve_snapshot(reference, ticket)` takes a ticket.
- A store lists its snapshots and its outputs with one query object each: `SnapshotQuery` and `OutputQuery`.
- A `SnapshotQuery` with no `targets` lists the snapshots of every target, newest first. `limit` and `offset` cut one page.
- A `limit` or an `offset` out of range is refused.
- The label of a query matches the label as the reference writes it (`before swap` and `before_swap` are the same).
- A `SnapshotEntry` carries its `ticket` and its `source`. `StoreContract` checks the interface of a store backend.
- The constructor of a source takes its `ticket` by keyword, so a plugin that gives its arguments by position does not pass the capture time.
- The column `label` of a snapshot holds the label as the reference writes it, with an index. `captured_at` holds the second of the reference.
- SQLite and PostgreSQL get the columns `ticket` and `ticket_key`, with an index on `ticket_key`.
- A store of 0.2, or of a development build of 0.3.0, must be made again: every command stops with a message.
- The Snapshots page lists every snapshot of every target in one table, newest first, with **Captured**, **Target**, **Label**, **Ticket** and **Source**.
- The filters **Target**, **Label** and **Ticket** stay in the URL. **Target** is a picker of chips, so the page lists several targets.
- The page of a target and its timeline do not change.
- The file store has no index: a snapshot list reads each document it lists. `snapdelta list --target` reads only the targets it names.

### Removed

- The list of targets of the Snapshots page, with the count and the latest capture of each target, and its `q` filter.
- The grouping of the diff list by the start of the text before `: `; filter by ticket instead.
- The **Before → after** button of **New diff**, which only knew the labels `before` and `after`.

### Fixed

- The Profiles and Settings forms show "Unsaved changes" and ask before the page is left only while their values differ from the saved ones.
- A save that fails keeps that state: the values of the file are the baseline, and **Discard** restores them.
- A store of an older snapdelta is refused, the file store like SQLite and PostgreSQL.
- The PostgreSQL collation test is skipped, not failed, on a server without ICU or older than PostgreSQL 15.
- Deleting a profile keeps the comments above the other tables, and a new profile goes before the comment of the next table.
- A **Prune** error on **Admin > Store** shows in the Prune panel with the typed values, in place of the full error page.
- **Admin > About** shows the store location once, without the backend name before it.
- Creating a profile with an empty name says **Give a name**.
- Enter in a **Target** search no longer submits the form of the page.
- The theme toggle shows a sun in the light theme and a moon in the dark theme.
- The Admin screenshots of the guides show the current pages.
- A profile whose name is not ASCII opens at its group.
- `snapdelta describe` shows the note it sets, and not only the ticket.
- The ticket of a target is checked, so a ticket that is too long is refused before any target is fetched.
- The targets of a snapshot list are trimmed, so a name with spaces around it finds its snapshots.
- A run of the Activity whose id is not one of ours is left out, so a prune does not stop on it.
- An error of the PostgreSQL database while the tables are made is a message and not a traceback, and the pool is closed.
- The PostgreSQL store has an index that gives the order of the snapshots, so a page of the list does not sort every row.

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
