# Langfuse v4 in one container: the web app, the worker, and the four stores
# they need (Postgres, ClickHouse, Redis, MinIO), started and stopped together
# by entrypoint.sh.
#
# Upstream ships these as six containers, and for a production deployment
# that is right: each store scales, fails and is backed up on its own. Here
# they exist only to serve this one trace UI, so they move inside it, and
# every store listens on the container's own 127.0.0.1 - no network in
# compose.yaml can reach them. What this does NOT buy is memory: ClickHouse is
# most of Langfuse's footprint whichever box it runs in.
#
# Built from the upstream images, so `docker compose build --pull langfuse`
# picks up new Langfuse releases. If a release changes how the web or worker
# entrypoints work, this is the file that breaks first.

# ClickHouse's Alpine image runs its glibc-linked binary on musl by carrying a
# private copy of glibc next to musl. Collect that glibc so it can come along.
FROM clickhouse/clickhouse-server:25.12-alpine AS clickhouse
RUN mkdir -p /stage/lib \
 && cp -a /lib/ld-2.* /lib/ld-linux-* /lib/libc.so.6 /lib/libdl.so.2 \
          /lib/libm.so.6 /lib/libnss_dns.so.2 /lib/libnss_files.so.2 \
          /lib/libpthread.so.0 /lib/libresolv.so.2 /lib/librt.so.1 /stage/lib/ \
 && if [ -d /lib64 ]; then cp -a /lib64 /stage/; fi

# MinIO is one statically linked Go binary.
FROM cgr.dev/chainguard/minio AS minio

FROM docker.langfuse.com/langfuse/langfuse-worker:4 AS worker

FROM docker.langfuse.com/langfuse/langfuse:4
USER root

RUN apk add --no-cache bash su-exec postgresql17 postgresql17-client redis \
 && adduser -S -D -H -s /sbin/nologin clickhouse \
 && adduser -S -D -H -s /sbin/nologin minio

COPY --from=clickhouse /stage/ /
COPY --from=clickhouse /usr/bin/clickhouse /usr/bin/clickhouse
COPY --from=clickhouse /etc/clickhouse-server /etc/clickhouse-server
# The upstream image's override makes ClickHouse listen on every interface;
# this one keeps it on loopback (see clickhouse.xml).
RUN rm -f /etc/clickhouse-server/config.d/docker_related_config.xml
COPY clickhouse.xml /etc/clickhouse-server/config.d/agentlab.xml

COPY --from=minio /usr/bin/minio /usr/bin/minio
COPY --from=worker /app/worker /app/worker

# Every store keeps its data under /data, which is one named volume.
RUN mkdir -p /data /run/postgresql \
 && ln -s /data/clickhouse /var/lib/clickhouse

COPY entrypoint.sh /usr/local/bin/langfuse-all-in-one
RUN chmod 0755 /usr/local/bin/langfuse-all-in-one

WORKDIR /app
EXPOSE 3000
# --single-child: signals go to the entrypoint alone, which stops the six
# processes in order rather than all at once.
ENTRYPOINT ["dumb-init", "--single-child", "--", "/usr/local/bin/langfuse-all-in-one"]
CMD []
