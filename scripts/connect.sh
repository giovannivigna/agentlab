#!/usr/bin/env bash
# Point the gateway's `external` model at any OpenAI-compatible endpoint.
#
#   make connect
#       asks for the base URL, the model name and the API key (not echoed)
#
#   EXTERNAL_BASE_URL=https://api.groq.com/openai/v1 \
#   EXTERNAL_MODEL=llama-3.1-8b-instant EXTERNAL_API_KEY=... make connect
#       no questions - but the key ends up in your shell history
#
# It saves the three values to .env (which git ignores), recreates the gateway
# so it reads them, and sends one short request through the gateway to prove
# the path works. From then on, any client asks the gateway for the model
# "external" with its own gateway key; the provider key stays in the gateway.
set -uo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { cp .env.example .env && echo "created .env from .env.example"; }

env_get() { sed -n "s/^$1=//p" .env | tail -1; }
masked() { local k=$1; [ -z "$k" ] && { echo "none"; return; }; echo "${k:0:6}...${k: -4}"; }

cur_url=$(env_get EXTERNAL_BASE_URL); cur_url=${cur_url:-https://api.openai.com/v1}
cur_model=$(env_get EXTERNAL_MODEL);  cur_model=${cur_model:-gpt-4o-mini}
cur_key=$(env_get EXTERNAL_API_KEY)
CLI=$(env_get CLI_GATEWAY_KEY); CLI=${CLI:-sk-agentlab-cli-local}

url=${EXTERNAL_BASE_URL:-}
model=${EXTERNAL_MODEL:-}
key=${EXTERNAL_API_KEY:-}

if [ -z "$url" ] || [ -z "$model" ] || [ -z "${EXTERNAL_API_KEY+set}" ]; then
    cat <<'EOF'
Any endpoint that speaks the OpenAI chat-completions API works. For example:

  provider    base URL                               model
  OpenAI      https://api.openai.com/v1              gpt-4o-mini
  OpenRouter  https://openrouter.ai/api/v1           openai/gpt-4o-mini
  Groq        https://api.groq.com/openai/v1         llama-3.1-8b-instant
  Ollama      http://host.docker.internal:11434/v1   llama3.1:8b   (no key)

The gateway makes the call, from inside its container: for a server on this
machine use host.docker.internal, not localhost.

EOF
    if [ -z "$url" ]; then
        read -r -p "Base URL [$cur_url]: " url </dev/tty || exit 1
        url=${url:-$cur_url}
    fi
    if [ -z "$model" ]; then
        read -r -p "Model [$cur_model]: " model </dev/tty || exit 1
        model=${model:-$cur_model}
    fi
    if [ -z "${EXTERNAL_API_KEY+set}" ]; then
        if [ -n "$cur_key" ]; then hint="Enter keeps $(masked "$cur_key"); 'none' for no key"
        else hint="Enter for no key"; fi
        read -r -s -p "API key, not echoed ($hint): " key </dev/tty || exit 1
        echo
        if [ -z "$key" ]; then key=$cur_key; fi
    fi
fi
[ "$key" = none ] && key=

case "$url" in
    http://*|https://*) ;;
    *) echo "base URL must start with http:// or https:// (got: $url)"; exit 2 ;;
esac
case "$url" in
    *://localhost*|*://127.0.0.1*)
        echo "note: '$url' is the gateway container itself. For a server on this machine,"
        echo "      use host.docker.internal instead of localhost." ;;
esac
url=${url%/}

# Write the three values into .env, replacing any earlier ones. Python, not
# sed: URLs and keys are full of characters sed would treat as syntax.
EXTERNAL_BASE_URL=$url EXTERNAL_MODEL=$model EXTERNAL_API_KEY=$key python3 - <<'PY'
import os, re
path = ".env"
text = open(path).read()
for name in ("EXTERNAL_BASE_URL", "EXTERNAL_MODEL", "EXTERNAL_API_KEY"):
    line = f"{name}={os.environ[name]}"
    if re.search(rf"(?m)^{name}=", text):
        text = re.sub(rf"(?m)^{name}=.*$", lambda _: line, text)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
open(path, "w").write(text)
PY
echo
echo "saved to .env: EXTERNAL_BASE_URL=$url  EXTERNAL_MODEL=$model  EXTERNAL_API_KEY=$(masked "$key")"

# `up` rather than `restart`: a restart keeps the old environment.
echo "recreating the gateway so it reads them..."
docker compose up -d --wait gateway >/dev/null 2>&1 || {
    echo "the gateway did not come up - see: docker compose logs gateway"; exit 1; }
docker compose run --rm gateway-keys >/dev/null 2>&1 || true   # in case it was a fresh gateway

# The reply parser, in a heredoc so it can quote freely (and run on the
# python3 that ships with macOS).
read -r -d '' PARSE <<'PY'
import json, sys
try:
    d = json.load(sys.stdin)
except ValueError:
    print("  no JSON back from the gateway - is it running? (make up)")
    sys.exit(1)
if "error" in d:
    err = d["error"]
    print("  the provider said no: " + str(err.get("message", err) if isinstance(err, dict) else err)[:400])
    print()
    print("  check the base URL (it usually ends in /v1), the model name and the key,")
    print("  then run make connect again.")
    sys.exit(1)
usage = d.get("usage") or {}
print("  " + (d["choices"][0]["message"].get("content") or "").strip())
print()
print("  model %s, %s tokens in, %s out" % (d.get("model"), usage.get("prompt_tokens"), usage.get("completion_tokens")))
PY

echo "sending one request through the gateway, as model \"external\", with the cli key..."
echo
curl -s localhost:4000/v1/chat/completions \
    -H "Authorization: Bearer $CLI" -H "Content-Type: application/json" \
    -d '{"model":"external","max_tokens":60,"messages":[{"role":"user","content":"Reply with one short sentence: which model are you?"}]}' \
  | python3 -c "$PARSE"
status=$?
[ "$status" -eq 0 ] && cat <<'EOF'

connected. Try it from anywhere that speaks the OpenAI API:
  make ask ASK_MODEL=external Q="..."
  AGENT_MODEL=external make run         # the pipeline's specialists, on your model
EOF
exit "$status"
