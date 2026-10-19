# Container image

The image `ghcr.io/antoinekh/snapdelta` runs `snapdelta-web`, with the core, the web UI and the PostgreSQL store. Each release publishes it with the PyPI packages, as `ghcr.io/antoinekh/snapdelta:vX.Y.Z` and `:latest`, for `linux/amd64` and `linux/arm64`.

```bash
docker run -d -p 8765:8765 -e SNAPDELTA_TOKEN="$(openssl rand -hex 32)" -v snapdelta-data:/data ghcr.io/antoinekh/snapdelta
```

| Path | Content |
|---|---|
| `/etc/snapdelta/snapdelta.toml` | The settings. The default listens on `0.0.0.0` with the token of `SNAPDELTA_TOKEN`; mount your own file over it. |
| `/data` | A volume: the default store, `/data/snapdelta/snapdelta.db`, and the files that relative paths of your settings name |

- A volume with a store of an older version (the directory `/data/snapdelta`) is kept with `SNAPDELTA_STORE=/data/snapdelta`, else the store looks empty after the upgrade.
- `snapdelta-web` refuses to start without `SNAPDELTA_TOKEN`, or with your own settings, without a login: see [api.md](api.md#access). Put a proxy with TLS in front, or give `tls_cert` and `tls_key`.
- The health check of the image calls `/healthz`, which needs no login and reads no store.
- The image runs as the user `snapdelta` (uid 10001); the volume must be writable by it.
- Every command of the CLI works inside: `docker exec CONTAINER snapdelta doctor`.

## Your plugins

The image holds no source: add your plugins in an image of your own.

```dockerfile
FROM ghcr.io/antoinekh/snapdelta:v0.3.0
RUN pip install --no-cache-dir my-snapdelta-plugin
COPY snapdelta.toml /etc/snapdelta/snapdelta.toml
COPY inventory /data/inventory
```

## A shared server with PostgreSQL

`docker/compose.yaml` starts the web UI and a PostgreSQL server, with the settings of `docker/compose.toml`:

```bash
export SNAPDELTA_TOKEN="$(openssl rand -hex 32)" SNAPDELTA_DB_PASSWORD="$(openssl rand -hex 16)"
docker compose -f docker/compose.yaml up -d
```

To use your plugins, set `image:` to your own image, and add your settings to `docker/compose.toml`.

## Build it

```bash
docker build -t snapdelta .
```
