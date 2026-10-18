# API

`snapdelta-web` serves a JSON API next to the web UI, under `/api/v1`. A pipeline uses it to take a snapshot before a change and after it, then to compare them; an AI agent uses it to check a device before it acts and after. The API does what the CLI does, over HTTP.

The OpenAPI description is at `/api/v1/openapi.json`, and an interactive page at `/api/v1/docs`: give the description to a tool or to an agent as the list of the calls that it can make.

## Access

| Server | Who can call | How |
|---|---|---|
| `127.0.0.1` (default), no token | programs on the same machine | no header |
| any address, with `[web] token` | anyone who has the token | `Authorization: Bearer TOKEN` |
| any address, with `[web] auth = "header"` | the users that a trusted reverse proxy lets in (SSO) | the proxy adds a header with the user name |
| any address, with `[web] auth = "none"` | anyone who reaches the server | no header, no login |
| any address, with the auth backend of a plugin | as the plugin decides: LDAP, OIDC | its login page, or what the plugin reads |

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
- With a token, the web pages ask for it once (**Log in**) and keep a signed session cookie for 12 hours; **Log out** ends it. Changing the token ends every session. The login page also asks for your name, which is not checked: Activity and the accepted changes show it.
- The API accepts the Bearer token, or the session cookie of a browser that logged in (so `/api/v1/docs` works in the browser).
- The access settings take effect at the next start of `snapdelta-web`: a save in **Admin > Settings** cannot open a running server.

### Users of a reverse proxy (SSO)

Behind a reverse proxy that does the login (oauth2-proxy, Authelia, Keycloak, a proxy with client certificates), `auth = "header"` takes the user from a header that the proxy adds:

```toml
[web]
host = "0.0.0.0"
auth = "header"

[auth.header]
header = "X-Forwarded-User"                   # the default
trusted_proxies = ["10.0.0.5", "10.0.1.0/24"]  # only these addresses may give the header; default: this machine
```

- A request needs the header and must come from an address of `trusted_proxies`: anyone can send the header, so a request from any other address is refused (`401`), for the pages, the API and MCP alike. The proxy must remove a header of the same name that a client sends.
- The server has no login page and no **Log out**: the proxy has them. The pages show the user name.

### Who did what

When the backend knows the user (a header of the proxy, the login of a plugin, the name given with the token), each run in Activity records it, and an accepted change takes it as its `by`, in place of a name that the form or the API request gives. The CLI records the login name of the system.

### Add an auth backend

The web UI declares its own extension point, `snapdelta.auth`, in the group `snapdelta.extension_points`, so `snapdelta plugins` and `snapdelta doctor` list its backends. A plugin adds a backend, such as LDAP or OIDC, with a subclass of `snapdelta_web.auth.AuthBackend`:

| Member | What it gives |
|---|---|
| `login_fields` | The fields of the login page (`LoginField(name, label, secret, required)`); none: no login page |
| `login(form)` | The `User` of a login form, or `None` when it proves none; the server then keeps a signed session cookie with the user name |
| `from_request(headers, client)` | The `User` that a request itself proves, such as a Bearer token or a header, or `None` |
| `access` | `Access.LOGIN` (default): every request needs a user; `LOCAL`: no user, this machine only; `OPEN`: no user, any address |
| `describe()` | One line for **Admin > About** |
| `settings_class` | Its `[auth.NAME]` table, a subclass of `snapdelta_web.auth.AuthSettings` |

```toml
[project.entry-points."snapdelta.auth"]
ldap = "my_plugin.auth:LdapAuth"
```

`snapdelta plugins new NAME --kind auth` writes a backend with a user name and a password, to start from.

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
| `GET /api/v1/plugins` | Every plugin of every extension point, the built-ins and the broken ones included, with the problems that `snapdelta doctor` finds |
| `GET /api/v1/sources` | The installed sources, and whether each fetches its data |
| `GET /api/v1/targets?q=R` | The targets that have snapshots |
| `GET /api/v1/snapshots?target=R1` | The snapshots of one target, newest first |
| `GET /api/v1/snapshots/{target}/{name}` | One snapshot: metadata and data |
| `POST /api/v1/snapshots` | Fetch targets and save a snapshot of each: `source`, `targets`, `groups`, `all_targets`, `label` |
| `POST /api/v1/diffs` | Compare: `left`, then `right` or `live`, with `models`, `profile`, `change`, `title`, `description`, `save`; `?include_output=true` returns the whole output |
| `POST /api/v1/diffs/targets` | For each target: `targets`, `groups` or `all_targets`, with the labels `left`, then `right` or `live`, and `change`, `title`, `description`; one diff per target, and the targets that `changed`, are `unchanged` or `failed` |
| `GET /api/v1/diffs?q=R1&limit=50` | The newest saved diffs whose target or change contains `q` |
| `GET /api/v1/diffs/{name}?format=json` | One saved diff: `json`, `text`, `markdown` or `csv` |
| `PATCH /api/v1/diffs/{name}` | Change its `change`, `title` or `description` |
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

# After the change, then one diff per host of the group
curl -sf -H "$AUTH" -X POST $API/snapshots -d '{"groups": ["core"], "label": "after"}' -H 'Content-Type: application/json'
curl -sf -H "$AUTH" -X POST $API/diffs/targets -H 'Content-Type: application/json' \
  -d '{"groups": ["core"], "left": "before", "right": "after", "profile": "change-window", "change": "CHG-1234"}' \
  | jq -e '.has_differences == false and .failed == {}'      # fails the step when a host changed or failed
```

`has_differences` is the result to check, and `changed` names the hosts that changed; the `url` of each diff opens it in the web UI, and `GET /api/v1/diffs/{name}?format=markdown` gives a report to attach to the change ticket. `{"groups": ["core"], "left": "before", "live": true}` takes the after snapshots and compares them in one call.

## An AI agent

An agent that talks MCP is better served by the MCP server, which gives the same operations as tools with their descriptions, and a guide ([mcp.md](mcp.md)). The API suits an agent that only makes HTTP calls.

An agent that changes a device can check its own work: before it acts, `POST /api/v1/snapshots` with the label `before`; after, `POST /api/v1/diffs` with `{"left": "R1@before", "live": true}` and `?include_output=true`. The output lists every object that only one side has and every changed attribute (see [output.md](output.md)), so the agent can compare what changed with what it meant to change. Give it `/api/v1/openapi.json` as the description of its tools.
