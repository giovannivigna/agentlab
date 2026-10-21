# The gateway: LiteLLM and the Postgres that holds its virtual keys and spend,
# in one container.
#
# One process per container is the usual rule, and this breaks it on purpose.
# The database exists only to serve this proxy, and putting it inside the
# proxy's container gives it the strongest boundary available: it listens on
# the container's own loopback, so no network - not `internal`, not `edge`,
# not the agent's - has any route to it at all. The cost is that the two
# start, stop and fail together, which for a key store that is useless
# without its proxy is the behaviour you want anyway.
#
# Pinned by digest, never by a moving tag: in October 2026 LiteLLM 1.107.0
# shipped an infostealer, and `main-stable` pointed at it. The digest is the
# multi-arch index of the v1.106.1 release; compose.yaml's gateway-keys pins
# the same one. To upgrade, check the release first, then take its digest from
# `docker buildx imagetools inspect ghcr.io/berriai/litellm:<tag>`.
FROM ghcr.io/berriai/litellm:v1.106.1@sha256:1acf80698f7eb47021b0bf5aed01ba9e80098cf74cdb3e588515bdd48a129fa6

# Wolfi's Postgres 17 pulls in a newer libcrypto whose package also ships
# /etc/ssl/openssl.cnf and ca.cnf, which the image's OpenSSL 3.6 already owns.
# --force-overwrite lets both install; Python (and so LiteLLM's TLS) keeps
# using OpenSSL 3.6, which `make check` exercises.
RUN apk add --no-cache --force-overwrite postgresql-17 postgresql-17-client \
 && addgroup -S postgres \
 && adduser -S -D -H -G postgres -s /bin/sh postgres \
 && mkdir -p /var/lib/postgresql/data /run/postgresql \
 && chown postgres:postgres /var/lib/postgresql/data /run/postgresql

COPY entrypoint.sh /usr/local/bin/gateway-entrypoint
RUN chmod 0755 /usr/local/bin/gateway-entrypoint

ENTRYPOINT ["/usr/local/bin/gateway-entrypoint"]
CMD ["--config", "/app/config.yaml", "--port", "4000"]
