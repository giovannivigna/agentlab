#!/usr/bin/env bash
# Verify the stack is wired up correctly for the current MODE. Run after
# `make up`.
#   make check
# Checks only what this MODE runs, and says `skip` for the rest - a check that
# silently does not run is how a suite goes green by accident.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0

# The Makefile exports MODE; run directly, fall back to .env.
MODE=${MODE:-$(sed -n 's/^MODE=//p' .env | tail -1)}; MODE=${MODE:-full}
case "$MODE" in 1) MODE=gateway;; 2) MODE=agent;; 3) MODE=kb;; 4) MODE=full;; esac
has() {  # has <part>: does this MODE run it?
    case "$1" in
        agent) [ "$MODE" != gateway ];;           # the pipeline and its MCP server
        kb)    [[ "$MODE" =~ ^(kb|full)$ ]];;
        obs)   [ "$MODE" = full ];;
    esac
}

KB_USER=$(sed -n 's/^KB_USER=//p' .env); KB_USER=${KB_USER:-agentlab}
KB_DB=$(sed -n 's/^KB_DB=//p' .env);     KB_DB=${KB_DB:-kb}
MASTER=$(sed -n 's/^GATEWAY_MASTER_KEY=//p' .env); MASTER=${MASTER:-sk-agentlab-master-local}
INTERN_KEY=$(sed -n 's/^INTERN_GATEWAY_KEY=//p' .env); INTERN_KEY=${INTERN_KEY:-sk-agentlab-intern-local}
CLI_KEY=$(sed -n 's/^CLI_GATEWAY_KEY=//p' .env); CLI_KEY=${CLI_KEY:-sk-agentlab-cli-local}

ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=1; }
skip() { printf '  skip  %s (not in MODE=%s)\n' "$1" "$MODE"; }

probe() {  # probe <description> <command...>
    local what="$1"; shift
    if "$@" >/dev/null 2>&1; then ok "$what"; else bad "$what"; fi
}

# Run python inside the agent container, on its network, with its mounts.
in_agent() { docker compose run --rm --no-deps --entrypoint python agent -c "$1" >/dev/null 2>&1; }
OUT='import socket;socket.setdefaulttimeout(4);socket.create_connection(("1.1.1.1",443))'

psql_q() { docker compose exec -T kb psql -U "$KB_USER" -d "$KB_DB" -tAc "$1" 2>/dev/null; }

echo "MODE=$MODE"
echo
echo "services"
# Probed from inside the network rather than through the published port: what
# matters is that the agent can reach these, and a host firewall or a busy
# port should not be able to make this script lie about that.
probe "gateway answers"           docker compose exec -T gateway \
      python -c "import urllib.request;urllib.request.urlopen('http://localhost:4000/health/liveliness',timeout=5)"
probe "gateway's key database runs inside it" docker compose exec -T gateway \
      pg_isready -q -h 127.0.0.1 -p 5432
probe "mock model answers"        docker compose exec -T mock-llm \
      python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/v1/models')"
if has agent; then
    probe "processing MCP server answers" docker compose exec -T mcp \
          python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health',timeout=5)"
else skip "processing MCP server"; fi
if has kb; then
    probe "knowledge base is healthy" docker compose exec -T kb pg_isready -U "$KB_USER" -d "$KB_DB"
else skip "knowledge base"; fi
if has obs; then
    probe "langfuse answers"      docker compose exec -T langfuse \
          wget -qO- http://127.0.0.1:3000/api/public/health
else skip "langfuse"; fi

# The gateway's keys. Asked from inside the gateway container, over its own
# loopback, so the answer is the gateway's policy and not the network's.
gw_status() {  # gw_status <key> [model] -> HTTP status: key lookup, or a chat call
    docker compose exec -T gateway python -c "
import json,sys,urllib.request,urllib.error
key, model = sys.argv[1], (sys.argv[2:] or [None])[0]
h={'Authorization':'Bearer '+key,'Content-Type':'application/json'}
r=(urllib.request.Request('http://localhost:4000/key/info', headers=h) if model is None
   else urllib.request.Request('http://localhost:4000/v1/chat/completions', headers=h,
        data=json.dumps({'model':model,'max_tokens':1,
                         'messages':[{'role':'user','content':'ping'}]}).encode()))
try: print(urllib.request.urlopen(r,timeout=10).status)
except urllib.error.HTTPError as e: print(e.code)
" "$@" 2>/dev/null
}

echo
echo "the gateway"
if [ "$(gw_status "$CLI_KEY" mock-model)" = "200" ]; then
    ok "your key (CLI_GATEWAY_KEY) can chat - make ask works"
else
    bad "your key cannot chat - run: make keys"
fi
intern_premium=$(gw_status "$INTERN_KEY" gpt-4o-mini)
if [ "$(gw_status "$INTERN_KEY")" != "200" ]; then
    bad "the intern's key is not known to the gateway - run: make keys"
elif [ "$intern_premium" = "401" ] || [ "$intern_premium" = "403" ]; then
    ok "per-agent model allowlist is enforced (intern cannot use gpt-4o-mini)"
else
    bad "the intern's key got $intern_premium for a model not on its list - is agents.yaml applied? (make keys)"
fi
# The gateway image carries a second OpenSSL for Postgres (gateway/Dockerfile).
# Python must still have a working TLS stack and CA bundle, or real providers fail.
probe "gateway's Python TLS has a CA bundle" docker compose exec -T gateway \
      python -c "import ssl,sys; sys.exit(0 if ssl.create_default_context().cert_store_stats()['x509_ca'] else 1)"
if has agent; then
    if in_agent "import os,sys; sys.exit(0 if os.environ.get('AGENT_API_KEY')=='$MASTER' else 1)"; then
        bad "agent holds the gateway master key - it can mint itself any key it likes"
    else
        ok "agent holds its own key, not the master key"
    fi
    if in_agent 'import socket;socket.setdefaulttimeout(4);socket.create_connection(("gateway",5432))'; then
        bad "agent can reach the gateway's database - it could rewrite its own budget"
    else
        ok "agent cannot reach the gateway's key database"
    fi
else skip "agent's gateway credentials"; fi

echo
echo "the tool servers"
if has agent; then
    # The remote one: the whole MCP handshake, from where the agent sits.
    if in_agent "
import asyncio, sys
from mcp import Client
async def main():
    async with Client('http://mcp:8000/mcp') as c:
        names = {t.name for t in (await c.list_tools()).tools}
    sys.exit(0 if {'process_text','process_image','process_binary'} <= names else 1)
asyncio.run(main())"; then
        ok "agent finds process_text/image/binary on the processing server"
    else
        bad "agent cannot list tools at http://mcp:8000/mcp - docker compose logs mcp"
    fi
    if docker compose exec -T mcp python -c "$OUT" >/dev/null 2>&1; then
        bad "processing server CAN reach the internet - it should be on internal only"
    else
        ok "processing server cannot reach the internet"
    fi
    # The local one: launched over stdio, as the pipeline launches it. It must
    # list the inputs, and refuse IDs that try to leave its directories.
    if in_agent "
import asyncio, sys
from app import clients
async def main():
    async with clients.connect() as (local, remote):
        ids = (await clients.call(local, 'list_inputs', {}))['ids']
        escape_read = await clients.call(local, 'read_input', {'id': '../../etc/passwd'})
        escape_write = await clients.call(local, 'write_output', {'id': '../run', 'content': 'x'})
    sys.exit(0 if ids and 'error' in escape_read and 'error' in escape_write else 1)
asyncio.run(main())"; then
        ok "local I/O server lists inputs and refuses IDs that escape"
    else
        bad "local I/O server failed - try: make shell, then python -m app.run"
    fi
else skip "MCP checks"; fi

if has kb; then
    echo
    echo "knowledge base"
    remembered=$(psql_q "SELECT count(*) FROM memory")
    if [ -n "$remembered" ]; then
        ok "memory table exists ($remembered result(s) remembered)"
    else
        bad "no memory table - run: make seed"
    fi
fi

if has obs; then
    echo
    echo "tracing"
    # The agent must reach Langfuse by name across `internal` - the HOSTNAME
    # trap in compose.yaml fails exactly this check and nothing else.
    if in_agent "import urllib.request;urllib.request.urlopen('http://langfuse:3000/api/public/health',timeout=5)"; then
        ok "agent reaches langfuse:3000"
    else
        bad "agent cannot reach langfuse:3000 - traces would go nowhere (see HOSTNAME in compose.yaml)"
    fi
    # Langfuse's four stores live inside its container, on loopback only.
    if in_agent "
import socket, sys
socket.setdefaulttimeout(3)
for port in (5432, 6379, 8123, 9000, 9100):
    try:
        socket.create_connection(('langfuse', port)); sys.exit(0)
    except OSError:
        pass
sys.exit(1)"; then
        bad "agent can reach one of Langfuse's stores - they should be on its loopback only"
    else
        ok "Langfuse's stores are unreachable from the agent (loopback only)"
    fi
fi

echo
echo "the boundary"
if has agent; then
    if in_agent "$OUT"; then
        bad "agent container CAN reach the internet - the internal network is not doing its job"
    else
        ok "agent container cannot reach the internet"
    fi
    if in_agent "import pathlib,sys; sys.exit(0 if pathlib.Path('/work/eval').exists() else 1)"; then
        bad "agent container can see eval/ - it can read its own answer key"
    else
        ok "agent container cannot see the answer key in eval/"
    fi
else skip "agent containment"; fi

echo
[ "$fail" -eq 0 ] && echo "all checks passed" || echo "some checks failed"
exit "$fail"
