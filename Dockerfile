# ro-admin: an administration API for an rAthena server you already run.
#
# Deliberately unprivileged. It needs exactly one thing -- a TCP connection to
# your MySQL -- and the container is built so that is all it CAN do:
#
#   * runs as a non-root user
#   * no Docker socket mount
#   * no `privileged`, no added capabilities
#   * no volumes; nothing on your host is writable from in here
#
# That list is not boilerplate. The service this replaces shipped a sidecar
# that ran as root, privileged, with /var/run/docker.sock mounted -- full
# control of the host's Docker daemon -- in order to pipe text into a stdin
# nobody read. A container should be able to demonstrate its blast radius by
# what it lacks.

FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src/ ./src/
# Wheel-only install into a prefix we can copy, so the runtime image carries no
# build tooling.
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
LABEL org.opencontainers.image.title="ro-admin" \
      org.opencontainers.image.description="Administration API for rAthena servers" \
      org.opencontainers.image.source="https://github.com/RafaDyck/ro-admin"

COPY --from=build /install /usr/local

# Non-root. Nothing here needs to write to disk at all.
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 roadmin
USER roadmin
WORKDIR /home/roadmin

EXPOSE 8000

# Every setting comes from the environment -- see .env.example. Notably the
# database host and port do too, which is what makes this work against
# someone else's install rather than only a sibling container.
#
# There is no CMD-level default for the secret on purpose: the service refuses
# to start without RO_ADMIN_JWT_SECRET, and a "helpful" default here would be a
# published credential in every deployment.
# Probes /healthz, which actually queries the database. An earlier version
# probed /openapi.json -- served from code needing no configuration at all --
# so a container with no database credentials reported "healthy" while being
# unable to answer a single real request. A check that cannot fail certifies
# nothing.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3     CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "ro_admin.main:app", "--host", "0.0.0.0", "--port", "8000"]
