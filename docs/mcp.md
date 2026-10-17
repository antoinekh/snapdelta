# MCP and the agent skill

An AI agent uses snapdelta in one of two ways:

| Way | For | Setup |
|---|---|---|
| **MCP server** | an agent that talks MCP (Claude Code, Claude Desktop, IDE agents, other MCP clients) | `snapdelta-mcp` over stdio, or `/mcp` of `snapdelta-web` over HTTP |
| **Skill** | an agent that runs shell commands | `skills/snapdelta/SKILL.md`: the CLI workflow and its rules |

The MCP server uses the official MCP Python SDK (`mcp`, its `MCPServer` class). It reads the same settings and store as the CLI and the web UI, and records each snapshot and diff in **Activity** with the origin `mcp`.

## Connect an agent

**Over stdio**, on the machine of the store: the agent starts `snapdelta-mcp`, which reads the settings file like the CLI (`--settings`, `SNAPDELTA_SETTINGS`, `./snapdelta.toml`, then the user file).

```bash
claude mcp add snapdelta -- snapdelta-mcp --settings /path/to/snapdelta.toml
```

Another client takes the same command in its configuration:

```json
{"mcpServers": {"snapdelta": {"command": "snapdelta-mcp", "args": ["--settings", "/path/to/snapdelta.toml"]}}}
```

**Over HTTP**, to a running `snapdelta-web`: the endpoint is `/mcp`. With a token, the agent sends it as for the API ([api.md](api.md)); with `auth = "none"`, no header.

```bash
claude mcp add --transport http snapdelta https://snapdelta.example.net/mcp --header "Authorization: Bearer $SNAPDELTA_TOKEN"
```

## See and try the server

`/mcp/docs` of `snapdelta-web` shows the server as an agent sees it, like `/api/v1/docs` for the API: each tool with its description and its parameters, the resources and the prompts, and the commands that connect an agent. **Try** under a tool calls it with JSON arguments and shows its answer, or its tool error. A browser that opens `/mcp` goes to this page; an MCP client gets the protocol. The page needs the login of the web UI when a token is set.

## What the server gives

| Kind | Name | What it does |
|---|---|---|
| Resource | `snapdelta://guide` | The words of snapdelta, the steps to check a change, and the rules for an agent |
| Tool | `list_sources`, `list_targets`, `list_snapshots` | The sources, the targets (with platform and groups), the snapshots of a target |
| Tool | `take_snapshots` | Fetch targets, groups or every target at the same time, with a label |
| Tool | `compare` | Two snapshots, or a snapshot and its target now (`live`), with a profile and a title; the first page of changes |
| Tool | `compare_targets` | For each target or each host of groups, `TARGET@left` with `TARGET@right` or the target now: the targets that changed, did not change or failed, and the name of each diff |
| Tool | `read_diff`, `list_diffs` | The changes of a saved diff by pages, each with its key; the newest diffs |
| Tool | `accept_changes`, `describe_diff` | Accept changes after a review, with a comment; give a title |
| Tool | `list_profiles`, `list_models` | The profiles of the settings; the models, their attributes and the fields never compared |
| Tool | `list_runs`, `read_run` | The activity, and the log of the loads of one run |
| Prompt | `check_change` | Before, change, after, compare, then accept what the change explains |
| Prompt | `review_diff` | Read every change of a saved diff and sort them |

A tool that fails returns the message of snapdelta (a wrong reference, a target that does not answer) as a tool error, so the agent can correct its call. The tools that only read say so to the client (`read_only_hint`); `take_snapshots`, a live `compare` and a live `compare_targets` read the devices and change nothing on them.

## Install the skill

Copy the directory of the skill where your agent finds its skills, for example for Claude Code:

```bash
cp -r skills/snapdelta ~/.claude/skills/        # every project
cp -r skills/snapdelta .claude/skills/          # one project
```

The skill tells the agent how to check a change with the CLI, how to read a failure with `snapdelta activity`, and what it must never do (accept a change that it did not check, delete the record of a change).
