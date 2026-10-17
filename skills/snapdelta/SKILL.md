---
name: snapdelta
description: Check a network or system change with snapdelta. Use it to take snapshots of devices before and after a change, compare them, read and accept the differences, find why a snapshot failed, or show a diff to a person in the web UI; with the `snapdelta` CLI, the snapdelta MCP tools when they are connected, or the JSON API of `snapdelta-web`.
---

# snapdelta

snapdelta takes snapshots of targets (routers, services) through source plugins, and compares two snapshots: one target before and after a change, or two targets. When the snapdelta MCP tools are connected (`take_snapshots`, `compare`, `read_diff`, ...), use them and read the resource `snapdelta://guide`; else use the CLI below.

## Where things are

| What | Where |
|---|---|
| Docs | `docs/` of the snapdelta repository, or <https://github.com/antoinekh/snapdelta/tree/main/docs>. Start with `getting-started.md` (a new project), `settings.md`, `profiles.md`, `inventory.md` |
| CLI help | `snapdelta --help`, `snapdelta COMMAND --help` |
| Web UI | `snapdelta-web` serves <http://127.0.0.1:8765> (port from `[web] port` or `--port`). Pages: `/diffs/NAME` (a diff), `/snapshots/TARGET` (a timeline), `/activity/RUN` (a run and its log) |
| JSON API | `/api/v1` of a running `snapdelta-web`; the calls at `/api/v1/docs`, the OpenAPI description at `/api/v1/openapi.json` (see `docs/api.md`) |
| MCP server | `snapdelta-mcp` over stdio, or `/mcp` of a running `snapdelta-web` over HTTP; `/mcp/docs` lists the tools and tries them (see `docs/mcp.md`) |

## Before you start

- In a uv project, run every command with `uv run` (`uv run snapdelta ...`): the sources are plugins installed in the environment of the project.
- Run the commands in the directory of the `snapdelta.toml` of the project, or give `--settings PATH`: it names the store, the inventory and the profiles.
- `snapdelta settings` shows the settings file and the store in use. `file: none, defaults` means no project settings and the default store `~/.local/share/snapdelta`: ask the user if this is the right place before you take snapshots.
- `snapdelta inventory` lists the targets with their platform (the source) and their groups; `snapdelta sources` lists the installed sources; `snapdelta doctor` checks the plugins, the settings, the store and the inventory when a command fails for no clear reason.
- No project yet: follow `docs/getting-started.md`, or show the demo of the README (the example plugin, in the web UI).
- A snapshot reference is `TARGET/NAME`, `TARGET@latest`, or `TARGET@LABEL` (the newest snapshot with that label, for example `r1@before`).

## Check a change

1. Snapshot before: `snapdelta snapshot --group GROUP --label before` (or `--target T` several times, or `--all-targets`). With no inventory, add `--source SOURCE`. Exit code 2 when a target fails: read its error, then `snapdelta activity` for the log.
2. Let the change happen. Do not apply a change yourself unless the user asked for it.
3. Snapshot after: the same command with `--label after`.
4. Compare every target at once: `snapdelta diff --group GROUP @before @after --title "CHG-1042: VLAN 20"` (or `--target T` several times, or `--all-targets`); add `--profile NAME` when a profile of the settings fits the change (`snapdelta settings` lists them). It prints one diff per target, then the targets that changed, did not change or failed. Exit code 0: no difference; 1: differences; 2: a target failed. One target: `snapdelta diff T@before T@after`.
5. Read the differences: the text output, or `snapdelta output NAME --format json` for the full document (`diff`: `-` is the left value, `+` the right value; keys `MODEL:UNIQUE_ID`).
6. Accept each change that the intent of the change explains: `snapdelta accept NAME KEY... --comment "planned in CHG-1042"`. Report every other change to the user, with its target and its values.

To compare a snapshot with the device now: `snapdelta diff T@before --live`, or for a group, `snapdelta diff --group GROUP @before --live`.

## Show a diff to the person

Start `snapdelta-web` in the same directory as the CLI, so it reads the same settings and store. It runs until it is stopped and does not open a browser: run it in the background, then give the person the link of the diff, `http://127.0.0.1:8765/diffs/NAME`. The person can filter the changes and accept them there.

## Find a problem

- `snapdelta activity` lists the recent runs (snapshots and diffs, from every client) with their status; `snapdelta activity RUN` shows the errors and the log of the loads of one run.
- `snapdelta show T@latest --model MODEL` prints one model of a snapshot; `snapdelta models` lists the models, their attributes, and the fields kept but never compared.

## Rules

- Never accept a change that you did not check against the intent of the change.
- Never delete or prune snapshots or diffs unless the user asked for it: they are the record of the change.
- A store holds the data of the devices: do not copy its files into a commit, a ticket or a chat.
- Do not change the access of `snapdelta-web` (`host`, `token`, `auth`) or add an MCP server to the configuration of an agent unless the user asked for it.
