# CLI

`snapdelta` is the command line interface: it takes the snapshots, compares them and prints the diffs. The web UI and the CLI use the same store and the same settings, so a command and a page see the same data.

```bash
snapdelta --help                 # the commands
snapdelta COMMAND --help         # the options of one command
```

## The options of every command

| Option | What it does |
|---|---|
| `--store SCHEME://ADDRESS` | The store to use: `sqlite://FILE`, a directory for the file store, or the scheme of a plugin backend. It wins over `SNAPDELTA_STORE` and the settings. |
| `--settings PATH` | The settings file. Else `SNAPDELTA_SETTINGS`, else `./snapdelta.toml`, else `~/.config/snapdelta/snapdelta.toml`. Every key is in [settings.md](settings.md). |
| `--version` | Print the versions and stop. |

## The commands

| Command | What it does |
|---|---|
| `snapshot` | Load input files (`FILES --source S`), or fetch targets (`--target`, `--group`, `--all-targets`, `--jobs N`); `--label`, `--ticket`, `--captured-at` |
| `diff LEFT RIGHT` | Compare two snapshots, or a snapshot and its target now (`--live`); or many targets at once, `diff --group core @before @after` (`--target`, `--all-targets`), one diff per target; `--model`, `--profile`, `--ticket`, `--note`, `--expect`, `--format`, `--no-save` |
| `output NAME` | Print a saved output again (`--format text`, `json`, `markdown`, `csv`) |
| `describe NAME` | Give a saved diff a ticket and a note, or change them (`--ticket`, `--note`) |
| `accept NAME KEY...` | Accept changes after a review (`--all`, `--by`, `--comment`, `--undo`); the Markdown report lists them |
| `list`, `show REF` | List the snapshots (newest first, with their ticket and source) and the outputs (`--target` keeps the targets and the outputs whose target or ticket has this text, `--label` the snapshots with that label, `--ticket` the snapshots and outputs of exactly that ticket); print the data of a snapshot (`--model`) |
| `delete`, `prune`, `info` | Delete snapshots (`delete snapshot REF...`) or outputs (`delete output NAME...`); keep the N newest (`--keep`) or the last days (`--older-than`), with the runs of Activity (`--dry-run`); show the store |
| `activity [RUN]` | The runs: each snapshot and saved diff, from the CLI, the web UI, the API or MCP, with the log of its loads (`--kind`, `--limit`) |
| `sources`, `models`, `inventory` | The installed sources; their models and attributes (`--source`); the hosts of the inventory (`--group`) |
| `plugins`, `plugins new NAME`, `doctor` | The plugins of every extension point; a new plugin package to start from (`--kind source`, `store`, `format`, `inventory`, `secret`, `notifier`, `rule`, `runner`, `auth`); a check of each plugin against its interface, then of the settings, the store and the inventory (exit code 1 on an error) |
| `settings` | The settings in use and where each value comes from (`--json`) |

## A change on many targets

Give the same ticket to the snapshots and to the diff. The ticket names the work, and the labels look inside it:

```bash
snapdelta snapshot --group core --label before --ticket CHG-1042
snapdelta snapshot --group core --label after  --ticket CHG-1042
snapdelta diff --group core @before @after --ticket CHG-1042 --note "VLAN 20"
```

The diff prints one output per target, then the targets that changed, that did not change and that failed. One target alone takes the two snapshots: `snapdelta diff R1@before R1@after`.

To compare a snapshot with the device now, use `--live`: the source of the left side fetches the target again, and that load is the right side.

```bash
snapdelta diff R1@before --live
```

## The references of a snapshot

A snapshot is named `TARGET/NAME`, `TARGET@latest` (the newest of the target) or `TARGET@LABEL` (the newest with that label, such as `R1@before`). With a ticket, the labels look among the snapshots of that ticket only. See [stores.md](stores.md).

## The expected changes

Before a change, write what it must do in a YAML or JSON file, then give it to `--expect`. The exit code is `0` when the diff holds the expected changes and no other, else `1`. See [output.md](output.md).

```bash
snapdelta diff R1@before R1@after --expect expected.yaml
```

## The exit codes

| Code | Meaning |
|---|---|
| `0` | No difference, or the expected changes and no other |
| `1` | Differences, or a diff that is not the expected changes |
| `2` | An error, or a target of a diff of many targets that failed |

So a script can check a change:

```bash
if ! snapdelta diff R1@before R1@after --format json > diff.json; then
  echo "the target changed"
fi
```

## The settings and the store

`snapdelta settings` shows the values in use and where each one comes from. The store can hold sensitive data: never commit it to a public repository. See [settings.md](settings.md) and [stores.md](stores.md).
