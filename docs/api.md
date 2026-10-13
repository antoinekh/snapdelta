# API

`snapdelta-web` serves a JSON API next to the web UI, under `/api/v1`. A pipeline uses it to take a snapshot before a change and after it, then to compare them; an AI agent uses it to check a device before it acts and after. The API does what the CLI does, over HTTP.

The OpenAPI description is at `/api/v1/openapi.json`, and an interactive page at `/api/v1/docs`: give the description to a tool or to an agent as the list of the calls that it can make.

## Access

| Server | Who can call | How |
|---|---|---|
| `127.0.0.1` (default), no token | programs on the same machine | no header |
| any address, with `[web] token` | anyone who has the token | `Authorization: Bearer TOKEN` |
| any address, with `[web] auth = "none"` | anyone who reaches the server | no header, no login |

```toml
[web]
host = "0.0.0.0"                             # or one address of the server
token = { env = "SNAPDELTA_TOKEN" }          # required on any address other than 127.0.0.1
tls_cert = "tls/cert.pem"                    # HTTPS; else put a proxy with TLS in front
tls_key = "tls/key.pem"
allowed_hosts = ["snapdelta.example.net"]    # the names of the server; empty: any
```

```bash
export SNAPDELTA_TOKEN="$(openssl rand -hex 32)"
snapdelta-web                                 # or --host 0.0.0.0 --tls-cert ... --tls-key ...
```

- `snapdelta-web` refuses to start on another address than `127.0.0.1` without a token, and warns when it runs there without TLS: the token would travel in clear.
- With a token, the web pages ask for it once (**Log in**) and keep a signed session cookie for 12 hours; **Log out** ends it. Changing the token ends every session.
- The API accepts the Bearer token, or the session cookie of a browser that logged in (so `/api/v1/docs` works in the browser).
- The access settings take effect at the next start of `snapdelta-web`: a save in **Admin > Settings** cannot open a running server.

### No login, on an isolated network

On a network that only trusted people and programs reach (a lab, a closed CI network, behind a proxy that does the login), `auth = "none"` removes the login of the pages and of the API, on any address:

```toml
[web]
host = "0.0.0.0"
auth = "none"                       # a token, if set, is ignored
allowed_hosts = ["10.0.0.5", "snapdelta.lab"]   # required on the network; "*" accepts any name
```

- Anyone who reaches the server can read every snapshot, delete diffs and fetch from the devices. `snapdelta-web` prints a warning at each start.
- `allowed_hosts` is required with another address than `127.0.0.1`: without the list, a web page open in a browser of the network could read the store through a DNS name of its own (DNS rebinding). List the addresses and the names that the clients use; `"*"` turns this check off.
- **Admin > About** shows the access of the running server.

## Calls

| Call | What it does |
|---|---|
| `GET /api/v1/version` | Versions of snapdelta, the web UI, diffsync, Python and the plugins |
| `GET /api/v1/sources` | The installed sources, and whether each fetches its data |
| `GET /api/v1/targets?q=R` | The targets that have snapshots |
| `GET /api/v1/snapshots?target=R1` | The snapshots of one target, newest first |
| `GET /api/v1/snapshots/{target}/{name}` | One snapshot: metadata and data |
| `POST /api/v1/snapshots` | Fetch targets and save a snapshot of each: `source`, `targets`, `groups`, `all_targets`, `label` |
| `POST /api/v1/diffs` | Compare: `left`, then `right` or `live`, with `models`, `profile`, `title`, `description`, `save`; `?include_output=true` returns the whole output |
| `GET /api/v1/diffs?q=R1&limit=50` | The newest saved diffs |
| `GET /api/v1/diffs/{name}?format=json` | One saved diff: `json`, `text`, `markdown` or `csv` |
| `PATCH /api/v1/diffs/{name}` | Change its `title` or `description` |
| `GET /api/v1/runs?kind=snapshot&limit=50` | The newest runs (snapshot or diff) and their result, without their log |
| `GET /api/v1/runs/{id}` | One run, with the log messages of its loads |
| `GET /api/v1/diffs/{name}/reviews` | The accepted changes, and `to_review`: the keys of the changes that nobody accepted yet |
| `POST /api/v1/diffs/{name}/reviews` | Accept one change: `key`, `by`, `comment`; `"accepted": false` withdraws it |

A snapshot reference is `TARGET/NAME`, `TARGET@latest`, or `TARGET@LABEL`: the newest snapshot of the target with that label. An error answers with a status `400` or `404` and `{"error": "..."}`.

Every call waits for its result: `POST /api/v1/snapshots` returns when every target is done, with `ok`, the reference of each saved snapshot, and the error of each failed target.

## A change in a pipeline

```bash
API=https://snapdelta.example.net/api/v1
AUTH="Authorization: Bearer $SNAPDELTA_TOKEN"

# Before the change: every host of the group core
curl -sf -H "$AUTH" -X POST $API/snapshots -d '{"groups": ["core"], "label": "before"}' -H 'Content-Type: application/json'

# ... apply the change ...

# After the change, then one compare per device
curl -sf -H "$AUTH" -X POST $API/snapshots -d '{"groups": ["core"], "label": "after"}' -H 'Content-Type: application/json'
curl -sf -H "$AUTH" -X POST $API/diffs -H 'Content-Type: application/json' \
  -d '{"left": "R1@before", "right": "R1@after", "profile": "change-window", "title": "CHG-1234 on R1"}' \
  | jq -e '.has_differences == false'      # fails the step when R1 changed
```

`has_differences` is the result to check; `url` opens the diff in the web UI, and `GET /api/v1/diffs/{name}?format=markdown` gives a report to attach to the change ticket.

## An AI agent

An agent that talks MCP is better served by the MCP server, which gives the same operations as tools with their descriptions, and a guide ([mcp.md](mcp.md)). The API suits an agent that only makes HTTP calls.

An agent that changes a device can check its own work: before it acts, `POST /api/v1/snapshots` with the label `before`; after, `POST /api/v1/diffs` with `{"left": "R1@before", "live": true}` and `?include_output=true`. The output lists every object that only one side has and every changed attribute (see [output.md](output.md)), so the agent can compare what changed with what it meant to change. Give it `/api/v1/openapi.json` as the description of its tools.
