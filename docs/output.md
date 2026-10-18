# Diff outputs

A compare gives an output: a JSON document (schema 2) that the store keeps and that every format reads.

## Expected changes

Before a change, write what it must do in a YAML or JSON file, and give it to `snapdelta diff --expect FILE` (or `expected` in `POST /api/v1/diffs`, `POST /api/v1/diffs/targets` and the MCP tools `compare` and `compare_targets`):

```yaml
- {model: vlan, object: "100", change: added}
- {model: interface, object: eth1, change: changed, attribute: mtu, left: 1500, right: 9000}
- {model: interface, object: "eth*", change: removed, target: R2}   # * matches any part; target: only this diff
```

The output then keeps `expectations`: the expected changes `done`, the `missing` ones, and the `unexpected` changes of the diff, after its profile. With `--expect`, the exit code is `0` when the diff holds the expected changes and no other, else `1`. `left` and `right` are optional: a side not given takes any value.

## The document

```json
{
  "schema": 2,
  "created_at": "2026-10-13T07:21:16Z",
  "versions": {"diffsync": "2.2.3", "snapdelta": "0.2.0"},
  "left":  {"target": "PE1", "source": "junos", "label": "before", "captured_at": "...", "snapshot": "PE1/20261013T072114Z-before"},
  "right": {"target": "PE2", "source": "sros",  "label": "after",  "captured_at": "...", "snapshot": "PE2/20261013T072115Z-after"},
  "compared": ["interface", "lag", "interface_state", "lag_state"],
  "skipped": [],
  "not_selected": [],
  "summary": {"create": 65, "update": 30, "delete": 31, "no-change": 10, "skip": 0},
  "diff": {"...": "see below"},
  "profile": null,
  "change": "CHG-1042",
  "title": "Swap of PE1 to PE2",
  "description": null,
  "reviews": {"interface:ge-0/0/6": {"by": "alice", "at": "2026-10-13T07:40:02Z", "comment": "planned"}}
}
```

| Key | Content |
|---|---|
| `left`, `right` | The two sides; `snapshot` is the reference of the snapshot in the store, `null` for a load that was not saved (`--live --no-save`, or a source given in Python) |
| `compared` | The top-level models of the compare |
| `skipped` | The top-level models that only one side loaded |
| `not_selected` | The models of both sides that `--model` left out |
| `summary` | The diffsync counts: `create` = only right, `delete` = only left, `update` = changed, `no-change` = unchanged; children included |
| `diff` | The diffsync `Diff.dict()`, below |
| `profile` | The profile of the compare, its rules and its count checks, or `null`; see [profiles.md](profiles.md) |
| `change` | The work that the diff belongs to (a ticket, an issue, any reference), or `null`; given at the compare or later (`snapdelta describe`), and found by the searches of diffs |
| `title`, `description` | What the diff is about, given at the compare or later (`snapdelta describe`) |
| `reviews` | The changes that a person accepted, by `MODEL:UNIQUE_ID` of the changed object: who, when, and an optional comment (`snapdelta accept`, **Accept** in the web UI) |

## The diff tree

`diff` maps each model to its objects by unique id. An object holds `-` (the left values), `+` (the right values), or both, and its children by model:

```json
{
  "interface": {
    "ge-0/0/6": {"-": {"type": "ethernet", "mtu": 9212}},
    "1/1/3":    {"+": {"type": "ethernet", "mtu": 9212}},
    "lag-59": {
      "-": {"description": "to PE2"},
      "+": {"description": "to PE1"},
      "subinterface": {
        "lag-59__0": {"-": {"ipv4": ["172.16.99.5/30"]}, "+": {"ipv4": ["172.16.99.6/30"]}}
      }
    },
    "lag-10": {"subinterface": {"lag-10__0": {"-": {"enabled": false}, "+": {"enabled": true}}}}
  }
}
```

| Shape | Meaning |
|---|---|
| `-` only | The object is only on the left side; `-` holds all its attributes. |
| `+` only | The object is only on the right side; `+` holds all its attributes. |
| `-` and `+` | The object is on both sides; both hold only the attributes that differ. |
| neither | The object is the same on both sides; only one of its children differs. |

A unique id joins the identifiers of the object with `__`: the sub-interface `0` of `lag-59` is `lag-59__0`.

## Formats

`snapdelta diff` and `snapdelta output` print an output with `--format`; the web diff page has the same formats in its **Export** menu.

| Format | For |
|---|---|
| `text` (default) | A terminal: `- name` only left, `+ name` only right, `~ name` changed with `attribute: left -> right`, children indented under their model |
| `json` | A tool: the document above |
| `markdown` | A ticket or a merge request: the two sides, the counts, one table per model (object, change, attribute, left, right) |
| `csv` | A spreadsheet: one row per object of one side only and per changed attribute (`model,object,parents,change,attribute,left,right`) |

A plugin adds a format with a `snapdelta.render.Renderer(media_type, suffix, render)` in the entry point group `snapdelta.renderers`; `render` takes an `Output` and returns the text. `snapdelta.render.change_rows()` gives the changes as flat rows, as the Markdown and CSV formats use them.

## Exit codes

`diff` and `output` exit with `0` when the output holds no difference, `1` when it holds differences, and `2` on an error. With a profile, the differences that the profile accepts do not count.
