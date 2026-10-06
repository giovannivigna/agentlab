#!/bin/sh
# Start the key database, then LiteLLM against it; stop both together.
#
# Postgres listens on 127.0.0.1 only. Inside a container that address belongs
# to the container alone, so the database is reachable by LiteLLM and by
# nothing else in the stack.
set -eu

PGDATA=${PGDATA:-/var/lib/postgresql/data}
DB_PASSWORD=${GATEWAY_DB_PASSWORD:?GATEWAY_DB_PASSWORD must be set}
as_pg() { su postgres -s /bin/sh -c "$1"; }

# A named volume is created root-owned; hand it to postgres on every boot.
mkdir -p "$PGDATA"
chown postgres:postgres "$PGDATA"
chmod 0700 "$PGDATA"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "gateway: first boot, creating the key database"
    as_pg "initdb -D '$PGDATA' -U postgres --auth-local=trust --auth-host=scram-sha-256 --no-locale -E UTF8" >/dev/null
    as_pg "pg_ctl -D '$PGDATA' -o '-c listen_addresses=' -w start" >/dev/null
    as_pg "psql -v ON_ERROR_STOP=1 -q -U postgres" <<SQL
CREATE ROLE litellm LOGIN PASSWORD '$DB_PASSWORD';
CREATE DATABASE litellm OWNER litellm;
SQL
    as_pg "pg_ctl -D '$PGDATA' -w stop" >/dev/null
fi

as_pg "pg_ctl -D '$PGDATA' -o '-c listen_addresses=127.0.0.1' -l '$PGDATA/server.log' -w start" >/dev/null
echo "gateway: key database up on 127.0.0.1:5432"

export DATABASE_URL="postgresql://litellm:${DB_PASSWORD}@127.0.0.1:5432/litellm"

# LiteLLM in the background, so this shell is still here to stop Postgres
# cleanly when the container is told to stop - or when LiteLLM dies.
litellm "$@" &
proxy=$!
stop() {
    kill -TERM "$proxy" 2>/dev/null || true
    wait "$proxy" 2>/dev/null || true
    as_pg "pg_ctl -D '$PGDATA' -m fast -w stop" >/dev/null 2>&1 || true
}
trap 'stop; exit 0' TERM INT
status=0
wait "$proxy" || status=$?
as_pg "pg_ctl -D '$PGDATA' -m fast -w stop" >/dev/null 2>&1 || true
exit "$status"
