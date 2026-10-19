# snapdelta for AI agents

snapdelta takes snapshots of devices (routers, services, APIs) through source plugins, and compares two snapshots: of one target at two moments (before and after a change), or of two targets (two vendors, two twins).

## Words

- **Target**: what a snapshot describes, for example the router `r1`. An inventory gives each target a platform (its source) and groups.
- **Snapshot**: the data of one target at one moment, by model (`interface`, `bgp_peer`, ...). A label, such as `before`, names it.
- **Ticket**: free text that names the work, such as `CHG-1042`, `#216` or `Migration Lyon phase 2`. A snapshot and a diff can have one.
- **Snapshot reference**: `TARGET/NAME`, `TARGET@latest` (the newest), or `TARGET@LABEL` (the newest with that label, for example `r1@before`).
- **Note**: free text about one diff, on many lines when needed.
- **Diff**: the output of a compare, saved under a name. Each object is only on the left (removed), only on the right (added), or changed (its attributes differ).
- **Change key**: `MODEL:UNIQUE_ID` of a changed object, for example `interface:eth1.20`. A key is what `accept_changes` takes.
- **Profile**: rules of a compare from the settings: attributes to ignore, tolerances (`"5%"`), count checks, name pairs. `list_profiles` gives them.
- **Run**: each snapshot and saved diff, with the log of its loads (`list_runs`, `read_run`).

## Check a change

1. `list_targets` or the inventory groups: choose the targets.
2. Before the change: `take_snapshots` with `label: "before"` and the `ticket` of the work. Check `failed`: a failed target has no snapshot.
3. The change happens (you or a person apply it).
4. After the change: `take_snapshots` with `label: "after"` and the same `ticket`.
5. `compare_targets` with `left: "before"`, `right: "after"`, the same targets or groups, a profile when one fits, the same `ticket` and a `note` such as `VLAN 20`: one diff per target. For one target, `compare` with `left: "TARGET@before"`, `right: "TARGET@after"`.
6. `changed` lists the targets with differences: read the changes of each (`read_diff` with the name of its diff, by pages) and compare them with what the change meant to do. `failed` gives the reason of each target that has no diff.
7. `accept_changes` for each change that is expected, with a short comment; tell the person about every change that is not expected.

Give the same `ticket` to the snapshots and to the diff: the labels (`before`, `after`, `latest`) then stay inside that ticket, so another piece of work with the same labels never mixes in. Without a `ticket`, `TARGET@before` is the newest `before` of any ticket. Give the `ticket` when you take the snapshots, else their labels do not resolve with it. `list_diffs` takes a `ticket` to find the work again. `list_snapshots` takes `targets` (a list of names), a `label` and a `ticket`, and gives the newest snapshot first with its ticket and source. `describe_diff` sets the ticket or the note of a saved diff.

To compare a snapshot with the device now, without a second snapshot step: `compare` with `live: true`, or `compare_targets` with `live: true` for many targets at the same time.

## Rules

- Never accept a change that you did not check against the intent of the change.
- A snapshot can take seconds per target: take several targets in one `take_snapshots` call, which fetches them at the same time.
- A value that a profile accepts (ignored, the same by a rule such as case-insensitive, within tolerance) is not a difference.
- The models and their attributes differ per plugin: `list_models` gives them; a field marked not compared is kept for reading only.
- To show a diff to a person: the web UI of the same store, `snapdelta-web`, has a page per diff, `/diffs/NAME` (by default `http://127.0.0.1:8765/diffs/NAME`), where the person can filter the changes and accept them.
- The docs: <https://github.com/antoinekh/snapdelta/tree/main/docs>.
