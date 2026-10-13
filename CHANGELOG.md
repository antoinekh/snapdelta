# Changelog

## v0.1.0

### Added

- First version: snapshot any data through plugin sources (diffsync models), from input files or fetched from targets, and compare two snapshots at two moments or between two targets; fields kept but never compared.
- Compare profiles (ignore, tolerance, counts, pairs), a live compare, outputs in text, JSON, Markdown and CSV, titles and descriptions, and review: accept each change with a name and a comment.
- File and SQLite stores, an optional Nornir inventory, parallel loads, one `snapdelta.toml` settings file, and Activity: each snapshot and diff is a run with the log of its loads.
- CLI `snapdelta`, and web UI `snapdelta-web`: diffs with a change map and inline marks, a timeline per target, snapshots that run in the background with live progress, a command palette and keyboard shortcuts; no internet access needed.
- JSON API and MCP server for pipelines and AI agents (token login or none, network access, TLS), with `/api/v1/docs` and `/mcp/docs`, and a skill for agents that use the CLI.
- Plugin entry points for sources, stores, formats and inventories, with a settings table for each source (`[sources.NAME]`), store backend (`[stores.SCHEME]`) and inventory format (`[inventories.NAME]`); an example plugin, a lab with FRR and Nokia SR Linux, and guides.
