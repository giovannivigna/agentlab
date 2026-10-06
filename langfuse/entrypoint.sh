#!/bin/bash
# Run Langfuse and its four stores as one container.
#
#   Postgres    127.0.0.1:5432   accounts, projects, API keys, prompts
#   ClickHouse  127.0.0.1:8123   traces and observations (and :9000, native)
#   Redis       127.0.0.1:6379   the queue between web and worker
#   MinIO       127.0.0.1:9100   raw event payloads, before the worker ingests them
#   worker      :3030            moves events from MinIO/Redis into ClickHouse
#   web         :3000            the UI and the ingestion API - the only door in
#
# Start order: the four stores, then web (whose entrypoint runs the Postgres
# and ClickHouse migrations), then the worker. If any one process exits, the
# rest are stopped and the container exits, so `restart: unless-stopped` brings
# the whole thing back rather than leaving five healthy processes and a corpse.
set -euo pipefail

DATA=/data
log() { echo "langfuse: $*"; }

# Every credential below comes from the environment compose.yaml sets for
# Langfuse itself, so each store and its client always agree.
: "${CLICKHOUSE_USER:?}" "${CLICKHOUSE_PASSWORD:?}" "${REDIS_AUTH:?}"
: "${LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID:?}" "${LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY:?}"
: "${LANGFUSE_POSTGRES_PASSWORD:?}"

mkdir -p "$DATA"/postgres "$DATA"/clickhouse "$DATA"/minio/langfuse "$DATA"/redis
chown -R postgres:postgres "$DATA"/postgres /run/postgresql
chmod 0700 "$DATA"/postgres
chown -R clickhouse "$DATA"/clickhouse
chown -R minio "$DATA"/minio
chown -R redis "$DATA"/redis

# --- first boot -------------------------------------------------------------

if [ ! -s "$DATA/postgres/PG_VERSION" ]; then
    log "first boot: creating the Postgres cluster"
    pwfile=$(mktemp); printf '%s' "$LANGFUSE_POSTGRES_PASSWORD" > "$pwfile"; chown postgres "$pwfile"
    su-exec postgres initdb -D "$DATA/postgres" -U postgres --pwfile="$pwfile" \
        --auth-local=trust --auth-host=scram-sha-256 --no-locale -E UTF8 >/dev/null
    rm -f "$pwfile"
fi

# The ClickHouse user Langfuse connects as. The built-in `default` user, which
# has no password, is removed: loopback-only is one lock, this is the second.
hash=$(printf '%s' "$CLICKHOUSE_PASSWORD" | sha256sum | cut -d' ' -f1)
cat > /etc/clickhouse-server/users.d/agentlab.xml <<XML
<clickhouse>
  <users>
    <default remove="remove"/>
    <${CLICKHOUSE_USER}>
      <password_sha256_hex>${hash}</password_sha256_hex>
      <networks><ip>127.0.0.1</ip><ip>::1</ip></networks>
      <profile>default</profile>
      <quota>default</quota>
      <access_management>1</access_management>
    </${CLICKHOUSE_USER}>
  </users>
</clickhouse>
XML

# --- supervision -------------------------------------------------------------

declare -A NAME=()
start() {  # start <name> <command...>: run in the background and remember it
    local name=$1; shift
    "$@" &
    NAME[$!]=$name
    log "started $name (pid $!)"
}

wait_for() {  # wait_for <name> <seconds> <probe...>; stops everything on failure
    local name=$1 limit=$2; shift 2
    for _ in $(seq "$limit"); do
        stop_if_asked
        if "$@" >/dev/null 2>&1; then log "$name is ready"; return 0; fi
        sleep 1
    done
    log "$name did not become ready in ${limit}s; stopping the rest"
    stop_all
    exit 1
}

running() {  # running <pid>: alive and not a zombie waiting to be reaped
    local stat
    stat=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
    stat=${stat##*) }               # the state is the field after "(comm) "
    [ "${stat%% *}" != Z ]
}

stop_one() {  # stop_one <pid> <name> <signal> <seconds>, then SIGKILL
    local pid=$1 name=$2 sig=$3 limit=$4
    kill -"$sig" "$pid" 2>/dev/null || true
    for _ in $(seq $(( limit * 4 ))); do
        running "$pid" || break
        sleep 0.25
    done
    if running "$pid"; then
        log "$name did not stop within ${limit}s; killing it"
        kill -KILL "$pid" 2>/dev/null || true
    fi
    wait "$pid" 2>/dev/null || true
    log "stopped $name"
}

stop_all() {
    # Clients first, then the stores they write to. Each gets a deadline:
    #  - web answers SIGTERM by waiting a hard-coded 110 s (so a load balancer
    #    can drain it) before exiting. It holds no state of its own - that is
    #    all in the stores - so it gets 5 s and then SIGKILL;
    #  - the worker flushes and logs "Shutdown complete, exiting process..."
    #    within milliseconds - and then can linger, kept alive by some handle.
    #    Everything is flushed by then, so it too gets 5 s and then SIGKILL;
    #  - the stores get 30 s each to flush. SIGINT is Postgres's "fast" mode.
    local wanted pid
    for wanted in web worker minio redis clickhouse postgres; do
        for pid in "${!NAME[@]}"; do
            [ "${NAME[$pid]}" = "$wanted" ] || continue
            case "$wanted" in
                web|worker) stop_one "$pid" "$wanted" TERM 5 ;;
                postgres)   stop_one "$pid" postgres INT 30 ;;
                *)          stop_one "$pid" "$wanted" TERM 30 ;;
            esac
            unset "NAME[$pid]"
        done
    done
}
# The trap only raises a flag. The stopping happens in the main flow: a trap
# that runs while bash is inside `wait -n` cannot reliably start and reap new
# children (it hung on its first `sleep`), and stop_all needs to do both.
stop_requested=0
trap 'stop_requested=1' TERM INT
stop_if_asked() {
    if [ "$stop_requested" = 1 ]; then
        log "stopping"; stop_all; exit 0
    fi
}

# --- the four stores ---------------------------------------------------------

start postgres su-exec postgres postgres -D "$DATA/postgres" \
    -c listen_addresses=127.0.0.1 -c unix_socket_directories=/run/postgresql
start redis su-exec redis redis-server --bind 127.0.0.1 --port 6379 \
    --requirepass "$REDIS_AUTH" --maxmemory-policy noeviction \
    --dir "$DATA/redis" --save "" --appendonly no --loglevel warning
start clickhouse su-exec clickhouse /usr/bin/clickhouse server \
    --config-file=/etc/clickhouse-server/config.xml
start minio env MINIO_ROOT_USER="$LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID" \
                MINIO_ROOT_PASSWORD="$LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY" \
    su-exec minio /usr/bin/minio server --quiet \
        --address 127.0.0.1:9100 --console-address 127.0.0.1:9101 "$DATA/minio"

wait_for postgres   60 pg_isready -q -h 127.0.0.1 -p 5432
wait_for redis      30 redis-cli -a "$REDIS_AUTH" --no-auth-warning ping
wait_for clickhouse 90 wget -qO- http://127.0.0.1:8123/ping
wait_for minio      30 wget -qO- http://127.0.0.1:9100/minio/health/live

# --- Langfuse ----------------------------------------------------------------

# Next.js binds $HOSTNAME. bash may have its own idea of that variable, so it
# is set here, explicitly: all interfaces, so the agent on `internal` and the
# host on `edge` both reach the UI and the ingestion API.
export HOSTNAME=0.0.0.0
cd /app
# web's entrypoint applies the Postgres and ClickHouse migrations, then serves.
start web su-exec nextjs ./web/entrypoint.sh node ./web/server.js --keepAliveTimeout 110000
wait_for web 300 wget -qO- http://127.0.0.1:3000/api/public/health
start worker su-exec nextjs ./worker/entrypoint.sh node worker/dist/index.js
stop_if_asked
log "all six processes up"

# Block until a signal arrives or any child exits; either way, stop them all.
# A signal makes `wait -n` return early with no pid; a child exiting makes it
# return that child's pid.
set +e
while true; do
    exited=
    wait -n -p exited
    status=$?
    stop_if_asked
    if [ -n "$exited" ]; then
        log "${NAME[$exited]:-a process} exited with status $status; stopping the rest"
        unset "NAME[$exited]"
        stop_all
        exit "$(( status == 0 ? 1 : status ))"
    fi
done
