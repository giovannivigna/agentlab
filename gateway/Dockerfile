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
FROM ghcr.io/berriai/litellm:main-stable

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
