# snapdelta-web with the core and the PostgreSQL store; add your plugins in an image of your own:
#   FROM ghcr.io/antoinekh/snapdelta
#   RUN pip install --no-cache-dir my-snapdelta-plugin
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build
WORKDIR /src
COPY . .
RUN for package in snapdelta snapdelta-web snapdelta-postgres; do \
      uv build --package "$package" --wheel --out-dir /dist; \
    done

FROM python:3.13-slim-bookworm
COPY --from=build /dist /dist
RUN pip install --no-cache-dir /dist/*.whl && rm -rf /dist \
    && useradd --uid 10001 --create-home --home-dir /home/snapdelta snapdelta \
    && mkdir -p /data && chown snapdelta /data
COPY docker/snapdelta.toml /etc/snapdelta/snapdelta.toml
# The settings file is /etc/snapdelta/snapdelta.toml, and the default store /data/snapdelta/snapdelta.db.
ENV XDG_CONFIG_HOME=/etc XDG_DATA_HOME=/data PYTHONUNBUFFERED=1
USER snapdelta
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8765
# No proxy: an HTTP_PROXY of the host must not take the request to the server itself.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD ["python", "-c", "import urllib.request as r; r.build_opener(r.ProxyHandler({})).open('http://127.0.0.1:8765/healthz', timeout=4)"]
CMD ["snapdelta-web"]
