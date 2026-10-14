# ecadlib team server. Normally started with `python3 -m ecadlib team setup` (docker-compose.yml: ecadlib + Caddy).
#   docker build -t ecadlib .
# The image holds no users, passwords, keys or certificates: everything lives in the /data volume
# (/data/workspace: users, plans, reports, audit log; /data/libs: KiCad libraries to index).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/data

# Fixed numeric uid/gid so a bind-mounted /data or certificate directory can be chowned to it (10001:10001).
RUN groupadd --system --gid 10001 ecadlib \
 && useradd --system --uid 10001 --gid ecadlib --no-create-home --home-dir /data --shell /usr/sbin/nologin ecadlib

WORKDIR /src
COPY pyproject.toml README.md LICENSE NOTICE MANIFEST.in ./
COPY ecadlib ./ecadlib
RUN pip install ".[all]" \
 && rm -rf /src \
 && mkdir -p /data/workspace /data/libs \
 && chown -R ecadlib:ecadlib /data
COPY docker/healthcheck.py /usr/local/bin/ecadlib-healthcheck

USER ecadlib
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "/usr/local/bin/ecadlib-healthcheck"]

ENTRYPOINT ["ecadlib"]
# Default: serve HTTPS itself with a certificate mounted at /certs (bring your own certificate). It refuses to
# start without one, and without an admin. docker-compose.yml overrides this to run plain HTTP behind Caddy.
CMD ["ui", "--team", "--workspace", "/data/workspace", "--lib", "/data/libs", "--host", "0.0.0.0", "--port", "8765", \
     "--no-browser", "--cert", "/certs/cert.pem", "--key", "/certs/key.pem"]
