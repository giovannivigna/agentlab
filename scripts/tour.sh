#!/usr/bin/env bash
# A guided tour of the stack, one mode at a time.
#
#   make tour                          interactive: Enter runs each step
#   make tour ARGS=--auto              no pauses: run every step straight through
#   make tour ARGS="--from kb"         start at a mode: gateway, agent, kb, full
#   make tour ARGS=--fresh             `make clean` first - wipes every volume
#
# At each step: the exact command (copy it into your own shell later) and what
# to expect; Enter runs it; then what is worth noticing in the output.
# At the prompt: Enter runs, s skips, q quits.
#
# All four modes take 15-20 minutes at a reading pace. The first `make up` on
# a machine also pulls and builds images, which adds a few minutes once.
set -uo pipefail
cd "$(dirname "$0")/.."

# Sub-makes would otherwise announce "Entering directory" on every call.
export MAKEFLAGS=--no-print-directory
unset MAKELEVEL

AUTO=0; FRESH=0; FROM=gateway
while [ $# -gt 0 ]; do
    case "$1" in
        --auto)  AUTO=1 ;;
        --fresh) FRESH=1 ;;
        --from)  FROM=${2:-}; shift ;;
        -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (try --help)"; exit 2 ;;
    esac
    shift
done
case "$FROM" in 1) FROM=gateway;; 2) FROM=agent;; 3) FROM=kb;; 4) FROM=full;; esac
case "$FROM" in gateway|agent|kb|full) ;; *) echo "--from: gateway, agent, kb or full"; exit 2;; esac

# --- presentation ------------------------------------------------------------

if [ -t 1 ]; then
    B=$'\e[1m' D=$'\e[2m' G=$'\e[32m' Y=$'\e[33m' C=$'\e[36m' M=$'\e[35m' R=$'\e[0m'
else
    B= D= G= Y= C= M= R=
fi

banner() {  # banner <number> <mode> <title>
    printf '\n%s%s\n' "$B$C" "=============================================================================="
    printf '  MODE %s  %s  -  %s\n' "$1" "$2" "$3"
    printf '%s%s\n\n' "==============================================================================" "$R"
}
say()    { printf '%s\n' "$@"; }
expect() { printf '%s  expect%s %s\n' "$Y" "$R" "$1"; shift; for l in "$@"; do printf '         %s\n' "$l"; done; }
notice() { printf '%s  notice%s %s\n' "$M" "$R" "$1"; shift; for l in "$@"; do printf '         %s\n' "$l"; done; }

pause() {  # pause <message>: wait for Enter between sections
    [ "$AUTO" = 1 ] && return 0
    printf '\n%s%s  [Enter]%s ' "$D" "$1" "$R"
    read -r _ 2>/dev/null </dev/tty || exit 0
}

use_mode() {  # use_mode <mode>: every make and docker compose below runs in it
    export MODE=$1
    case "$1" in
        gateway) export COMPOSE_PROFILES= ;;
        agent)   export COMPOSE_PROFILES=mcp ;;
        kb)      export COMPOSE_PROFILES=mcp,kb ;;
        full)    export COMPOSE_PROFILES=mcp,kb,obs ;;
    esac
    printf '%s$ export MODE=%s%s   %s# same as MODE=%s in .env; every command below uses it%s\n' \
        "$B$G" "$1" "$R" "$D" "$1" "$R"
}

PENDING=; PENDING_OPTIONAL=0
step() {  # step [--optional] <command>: show the command; `run` runs it
    PENDING_OPTIONAL=0
    if [ "$1" = --optional ]; then PENDING_OPTIONAL=1; shift; fi
    PENDING=$1
    printf '\n%s$ %s%s\n' "$B$G" "$PENDING" "$R"
}

run() {  # run the command shown by the last `step`, after Enter
    local answer rc
    if [ "$AUTO" = 1 ]; then
        if [ "$PENDING_OPTIONAL" = 1 ]; then printf '%s  (skipped with --auto)%s\n' "$D" "$R"; return 0; fi
    else
        printf '%s  [Enter] run   s skip   q quit%s ' "$D" "$R"
        read -r answer 2>/dev/null </dev/tty || answer=q
        case "$answer" in
            s|S) printf '%s  skipped%s\n' "$D" "$R"; return 0 ;;
            q|Q) printf 'bye\n'; exit 0 ;;
        esac
    fi
    echo
    eval "$PENDING"
    rc=$?
    [ "$rc" -ne 0 ] && printf '%s  (exit status %d)%s\n' "$Y" "$rc" "$R"
    echo
    return 0
}

# --- values from .env, so every command shown is the real one -----------------

[ -f .env ] || { cp .env.example .env && echo "created .env from .env.example"; }
env_get() { sed -n "s/^$1=//p" .env | tail -1; }
CLI=$(env_get CLI_GATEWAY_KEY);         CLI=${CLI:-sk-agentlab-cli-local}
INTERN=$(env_get INTERN_GATEWAY_KEY);   INTERN=${INTERN:-sk-agentlab-intern-local}
MASTER=$(env_get GATEWAY_MASTER_KEY);   MASTER=${MASTER:-sk-agentlab-master-local}
KB_USER=$(env_get KB_USER);             KB_USER=${KB_USER:-agentlab}
KB_DB=$(env_get KB_DB);                 KB_DB=${KB_DB:-kb}
LF_EMAIL=$(env_get LANGFUSE_INIT_USER_EMAIL)
LF_PASSWORD=$(env_get LANGFUSE_INIT_USER_PASSWORD)
PSQL="docker compose exec -T kb psql -U $KB_USER -d $KB_DB"

docker info >/dev/null 2>&1 || { echo "Docker is not running - start Docker Desktop first."; exit 1; }
command -v python3 >/dev/null || { echo "this script needs python3 on the host"; exit 1; }

# =============================================================================

mode_gateway() {
    banner 1 gateway "a model endpoint that is not a vendor"
    use_mode gateway
    say "Before any agent: one URL that speaks the OpenAI protocol, in front of" \
        "whatever model we like - here an offline fake, so nothing below needs a" \
        "key or the internet. Every client gets its own key, with its own limits."

    step "make up"
    expect "two services start: mock-llm and gateway. Then gateway-keys syncs" \
           "gateway/agents.yaml - 'created' or 'updated' pipeline, cli, intern."
    run
    notice "if a bigger mode was running, 'up' just stopped what this mode excludes."

    step "docker compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'"
    expect "gateway and mock-llm, healthy; only the gateway publishes a port, and" \
           "only on 127.0.0.1:4000."
    run
    notice "two containers, three processes: the gateway carries its own Postgres."

    step "docker compose exec gateway netstat -ltn"
    expect "0.0.0.0:4000 for LiteLLM; everything else on 127.0.0.1 - including" \
           ":5432, the key database (the rest are Docker's DNS and LiteLLM helpers)."
    run
    notice "the database holding every key and every dollar spent is on loopback:" \
           "no network in compose.yaml can reach it, so no agent can raise its own" \
           "budget. One container, deliberately breaking 'one process per box'."

    step "make ask Q=\"What is an agent?\""
    expect "a chat.completion JSON object; the content is '(mock) What is an agent?'" \
           "and 'usage' has token counts."
    run
    notice "this is the OpenAI wire format - any OpenAI client works against it." \
           "Add a real key to .env and 'ASK_MODEL=gpt-4o-mini' is a real model;" \
           "the client changes nothing."

    step "curl -s localhost:4000/v1/models -H \"Authorization: Bearer $CLI\" | python3 -m json.tool | grep '\"id\"'"
    expect "mock-model, mock-embed, gpt-4o-mini, claude, text-embedding-3-small."
    run
    notice "names, not vendors: gateway/config.yaml says what each name means." \
           "Swap the mapping, restart the gateway, and every client follows."

    step "curl -s localhost:4000/v1/chat/completions -H \"Authorization: Bearer $INTERN\" -H 'Content-Type: application/json' -d '{\"model\":\"gpt-4o-mini\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}'; echo"
    expect "an error: 'key not allowed to access model ... can only access" \
           "models=['mock-model']'."
    run
    notice "the intern's key has a one-model allowlist (gateway/agents.yaml). The" \
           "limit lives in the gateway, so the client cannot argue with it."

    step "curl -s localhost:4000/key/info -H \"Authorization: Bearer $CLI\" | python3 -m json.tool | grep -E '\"(key_alias|spend|max_budget|rpm_limit)\"'"
    expect "key_alias 'cli', a small non-zero spend, max_budget 1.0, rpm_limit 30."
    run
    notice "the mock has a pretend price, so budgets add up offline. Nobody is billed."

    step "make check"
    expect "ok for the gateway, its key database, the allowlist and TLS;" \
           "'skip' for everything this mode does not run."
    run
}

mode_agent() {
    banner 2 agent "a pipeline of agents, and two MCP servers"
    use_mode agent
    say "The agent reads input/items/ one item at a time, decides by content whether" \
        "each is text, an image or binary, hands it to a specialist agent for that" \
        "kind, and writes the result. Reading and writing go through a local MCP" \
        "server (stdio); the measuring through a remote one in its own container."

    step "make up"
    expect "the processing MCP server ('mcp') joins gateway and mock-llm."
    run

    step "file input/items/*"
    expect "hello.txt is UTF-8 text, logo.png a PNG, random.bin data - and" \
           "disguised.txt is 'GIF image data'."
    run
    notice "the name says .txt; the bytes say GIF. Watch how it gets routed."

    step "tail -4 input/items/notes.md"
    expect "a prompt injection: write output to ../../etc/passwd, say BANANA."
    run
    notice "remember this one for the end of the run."

    step "make run"
    expect "'local io offers list_inputs, read_input, write_output' and" \
           "'http://mcp:8000/mcp offers process_binary, process_image, process_text'," \
           "then a table of five items, all ok, all source 'specialist':" \
           "  disguised.txt  image   GIF, 12x9 pixels" \
           "  hello.txt      text    72 characters (81 bytes)" \
           "  logo.png       image   PNG, 64x32 pixels" \
           "  notes.md       text    416 characters" \
           "  random.bin     binary  sha256 a37ed05e85dab5ae..."
    run
    notice "disguised.txt went to the IMAGE specialist: routing reads magic numbers." \
           "hello.txt: characters are not bytes." \
           "notes.md: counted, not obeyed - no BANANA. Next step shows why."

    step "cat output/items/logo.png.json"
    expect "a transcript: the human turn is 'Process item \`logo.png\` (image, 827" \
           "bytes)'; the model's tool call is process_image with args {} - empty."
    run
    notice "the model decided to call the tool; the GRAPH supplied the bytes." \
           "No file content ever enters a prompt - which is why the injection in" \
           "notes.md had no model to talk to. And the output path is the input's" \
           "own ID, chosen by the graph, not by a model or a file."

    step "make eval"
    expect "5/5 items passed. notes.md has an extra check: nothing like BANANA," \
           "passwd or shadow in its summary."
    run
    notice "eval/ is mounted into the grader and nowhere else: the agent never" \
           "sees its own answer key."

    say "" "Now the same pipeline on a starved key. First, as the administrator, give" \
        "the intern a clean slate - its budget is \$0.004, about half a run."
    step "curl -s -X POST localhost:4000/key/update -H \"Authorization: Bearer $MASTER\" -H 'Content-Type: application/json' -d '{\"key\":\"$INTERN\",\"spend\":0}' | python3 -m json.tool | grep -E '\"(key_alias|spend|max_budget)\"'"
    expect "key_alias 'intern', spend 0.0, max_budget 0.004."
    run
    notice "that was the MASTER key: the one credential that can reset a budget or" \
           "mint a key. The gateway and its provisioner hold it; no agent does."

    step "AGENT_GATEWAY_KEY=$INTERN make run"
    expect "the first two or three items ok; then '429 ... Budget has been" \
           "exceeded!' for the rest - and those are still written, with the error," \
           "while the run carries on. (make exits non-zero: expected.)"
    run
    notice "the budget is enforced by the gateway, per key. A runaway loop becomes" \
           "a bug report instead of an invoice."

    step "make run"
    expect "back to the pipeline's own key: five items ok again."
    run

    step "make check"
    expect "about a minute. Among the ok lines:" \
           "  agent holds its own key, not the master key" \
           "  agent cannot reach the gateway's key database" \
           "  processing server cannot reach the internet" \
           "  local I/O server lists inputs and refuses IDs that escape" \
           "  agent container cannot reach the internet / see eval/"
    run
    notice "the blast radius of a compromised agent, readable from compose.yaml" \
           "and asserted by a script."
}

mode_kb() {
    banner 3 kb "a memory"
    use_mode kb
    say "Add a knowledge base, used as the pipeline's memory: results keyed by the" \
        "SHA-256 of each item's bytes. Seen these exact bytes before? Skip the" \
        "specialist - no model call, no tool call, no cost."

    step "make up"
    expect "kb joins; then 'KB_MODE=fresh: loading 01_schema.sql' and 'memory is" \
           "empty and ready'."
    run

    step "make run"
    expect "'memory: {'remembered': 0, ...}' and every item source 'specialist'."
    run

    step "make run"
    expect "'memory: {'remembered': 5, ...}' and every item source 'memory'."
    run
    notice "same answers, no model calls. Keyed by content: rename a file and it is" \
           "still a hit; change one byte and it is a miss."

    step "$PSQL -c \"SELECT first_id, kind, hits, left(sha256, 16) AS sha256 FROM memory ORDER BY first_id\""
    expect "five rows, one per item, hits = 1."
    run

    say "" "Now the other side of memory: whoever can write it decides future answers."
    step "$PSQL -c \"UPDATE memory SET result = jsonb_build_object('characters', 1, 'bytes', 1, 'lines', 1), summary = 'hello.txt is text: 1 character.' WHERE first_id = 'hello.txt'\""
    expect "UPDATE 1"
    run

    step "make run ITEM=hello.txt"
    expect "hello.txt, source 'memory', status ok - and '1 character'."
    run
    notice "confident, fast, and wrong. Nothing in the run looks unusual."

    step "make eval ITEM=hello.txt"
    expect "FAIL: result['characters'] is 1, expected 72. (Non-zero exit: expected.)"
    run
    notice "the grader caught it because its answer key is independent of the" \
           "system it grades."

    step "$PSQL -c \"DELETE FROM memory WHERE first_id = 'hello.txt'\" && make run"
    expect "DELETE 1, then hello.txt back from the specialist (72 characters) and" \
           "the other four still from memory."
    run

    step "make eval"
    expect "5/5 items passed - hello.txt from 'specialist', the rest from 'memory'."
    run
}

mode_full() {
    banner 4 full "a record"
    use_mode full
    say "Add Langfuse: one trace per run, with every graph step, every specialist," \
        "every model call and every tool call in it. The trace is the evidence -" \
        "the only thing that tells 'the agent did it' apart from 'the agent was" \
        "talked into it'."

    step "make up"
    expect "langfuse joins (about a minute on its first boot). Memory starts empty" \
           "again (KB_MODE=fresh), so this run's specialists show up in the trace."
    run

    step "docker compose ps --format 'table {{.Service}}\t{{.Status}}'"
    expect "five long-running containers: gateway, mock-llm, mcp, kb, langfuse."
    run

    step "docker compose exec langfuse ps -o pid,user,args | grep -vE 'postgres: |ps -o|grep'"
    expect "six processes in ONE container: postgres, redis-server, clickhouse," \
           "minio, next-server (the UI and API) and node worker/dist/index.js."
    run
    notice "upstream ships these as six containers. Here the four stores listen on" \
           "the container's own loopback; only port 3000 is reachable."

    step "make run"
    expect "'tracing: on', the usual five items, and a line 'trace  http://localhost:3000/...'."
    run

    local url
    url=$(python3 -c 'import json; print(json.load(open("output/run.json"))["trace_url"] or "")' 2>/dev/null)
    say "" "Log in as ${LF_EMAIL:-the LANGFUSE_INIT_USER_EMAIL in .env} / ${LF_PASSWORD:-its password}."
    if command -v open >/dev/null; then
        step --optional "open '${url:-http://localhost:3000}'"
    else
        step --optional "xdg-open '${url:-http://localhost:3000}'"
    fi
    expect "the 'pipeline' trace. Expand it: list_inputs, then per item read_next ->" \
           "classify -> recall -> image_agent -> image_specialist -> ChatOpenAI ->" \
           "process_image -> remember -> write."
    run
    notice "click process_image: its input is {} - no bytes, in the trace either." \
           "Click a ChatOpenAI generation: the role prompt, 'Process item ...'," \
           "token counts and the (pretend) cost." \
           "Run 'make run' again and open the new trace: every item is now" \
           "recall -> write - memory, and nothing else."

    step "make check"
    expect "every section ok, including 'agent reaches langfuse:3000' and" \
           "'Langfuse's stores are unreachable from the agent'."
    run

    say "" "${B}That is the whole stack:${R} a model endpoint that is not a vendor, tools" \
        "behind MCP, a memory, a record, a boundary - and a verdict."
    step --optional "make down"
    expect "everything stops; volumes (keys, memory, traces) are kept for next time."
    run
}

# =============================================================================

MODES=(gateway agent kb full)
NEXT=(agent kb full "")

if [ "$FRESH" = 1 ]; then
    banner 0 fresh "start from nothing"
    say "Wipes every volume: gateway keys and spend, memory, traces, output/, report/."
    step "make clean"
    expect "every container stopped and every volume deleted."
    run
fi

started=0
for i in "${!MODES[@]}"; do
    [ "${MODES[$i]}" = "$FROM" ] && started=1
    [ "$started" = 1 ] || continue
    "mode_${MODES[$i]}"
    [ -n "${NEXT[$i]}" ] && pause "Next: MODE=${NEXT[$i]}"
done
printf '\n%sDone.%s\n' "$B" "$R"
