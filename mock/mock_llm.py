"""An offline stand-in for a language model, speaking the OpenAI wire format.

It is a hand-written state machine, not a model: it inspects the conversation,
notices which tools it has been offered and which results have come back, and
emits the next correctly-shaped move. Here that means playing the pipeline's
three specialists (agent/app/specialists.py):

  * offered `process_text`, `process_image` or `process_binary` and no result
    yet: call it (process_binary with sha256, the specialist prompt's default);
  * a result has come back: say what it found, in one sentence;
  * offered no tools at all - `make ask` in MODE=gateway - echo the prompt.

Why a stack like this ships with one:

  * no API key, so anyone can `make up` with nothing but Docker;
  * no network, so the agent container can stay on an internal-only network
    and still do something;
  * deterministic, so a demo that worked yesterday works today, and so a test
    that fails means the *pipeline* changed, not that the model felt different.

The last point is the serious one. Swap this for a real model when you want to
know whether the model can do the task; keep it when you want to know whether
your graph, your tools and your plumbing are correct. Those are different
questions and they want different backends.
"""

import hashlib
import json
import math
import os
import re
import time
import uuid

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI(title="agentlab-mock-llm")

PROCESSORS = ("process_text", "process_image", "process_binary")
ITEM_ID = re.compile(r"`([^`]+)`")


def _as_dict(content) -> dict:
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except (json.JSONDecodeError, TypeError):
        return {"text": content or ""}


def _call(name: str, args: dict) -> dict:
    return {"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _describe(tool: str, item: str, r: dict) -> str:
    """The specialist's one-sentence report on a tool result."""
    if "error" in r:
        return f"`{item}` could not be processed: {r['error']}"
    if tool == "process_text":
        return (f"`{item}` is text: {r['characters']} characters "
                f"({r['bytes']} bytes, {r['lines']} lines).")
    if tool == "process_image":
        return (f"`{item}` is a {r['format']} image, "
                f"{r['width']}x{r['height']} pixels.")
    return (f"`{item}` is {r['bytes']} bytes of binary data; "
            f"its {r['algorithm']} is {r['hash'][:16]}...")


def _decide(messages: list[dict], offered: set[str]) -> dict:
    """Return an OpenAI `message`: either tool_calls, or a final answer.

    Decided from the current turn only - the messages after the last user
    message - so one item's result never leaks into the next.
    """
    last_user = max((i for i, m in enumerate(messages)
                     if m.get("role") == "user"), default=-1)
    text = (messages[last_user].get("content") or "") if last_user >= 0 else ""
    if isinstance(text, list):          # content parts, as some clients send
        text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))
    results = [_as_dict(m.get("content"))
               for m in messages[last_user + 1:] if m.get("role") == "tool"]

    tool = next((t for t in PROCESSORS if t in offered), None)
    if tool is None:
        return {"role": "assistant", "content": f"(mock) {text.strip()[:200]}"}

    if not results:
        args = {"algorithm": "sha256"} if tool == "process_binary" else {}
        return {"role": "assistant", "content": None,
                "tool_calls": [_call(tool, args)]}

    match = ITEM_ID.search(text)
    return {"role": "assistant",
            "content": _describe(tool, match.group(1) if match else "the item",
                                 results[-1])}


def _tokens(value) -> int:
    """A rough token count: about four characters a token, as for English.

    A real model reports exact counts; this one reports plausible ones, because
    the gateway prices and rate-limits by them. With zeros here, a budget or a
    tokens-per-minute limit in gateway/agents.yaml could never trip offline.
    """
    text = value if isinstance(value, str) else json.dumps(value)
    return max(1, len(text) // 4)


def _envelope(model: str, message: dict, prompt_tokens: int) -> dict:
    completion_tokens = _tokens(message)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message,
                     "finish_reason": "tool_calls" if message.get("tool_calls")
                                      else "stop"}],
        "usage": {"prompt_tokens": prompt_tokens,
                  "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    }


@app.post("/v1/chat/completions")
async def chat_completions(body: dict):
    model = body.get("model", "mock-model")
    offered = {spec["function"]["name"] for spec in body.get("tools") or []
               if spec.get("function", {}).get("name")}
    message = _decide(body.get("messages", []), offered)

    if not body.get("stream"):
        prompt_tokens = _tokens([body.get("messages", []), body.get("tools") or []])
        return _envelope(model, message, prompt_tokens)

    def sse():
        base = {"id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()), "model": model}
        if message.get("tool_calls"):
            for i, call in enumerate(message["tool_calls"]):
                delta = {"tool_calls": [{"index": i, **call}]}
                yield ("data: " + json.dumps({**base, "choices": [
                    {"index": 0, "delta": delta, "finish_reason": None}]}) + "\n\n")
        else:
            for word in (message.get("content") or "").split(" "):
                delta = {"content": word + " "}
                yield ("data: " + json.dumps({**base, "choices": [
                    {"index": 0, "delta": delta, "finish_reason": None}]}) + "\n\n")
        finish = "tool_calls" if message.get("tool_calls") else "stop"
        yield ("data: " + json.dumps({**base, "choices": [
            {"index": 0, "delta": {}, "finish_reason": finish}]}) + "\n\n")
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "256"))
_TOKEN = re.compile(r"[a-z0-9]+")


def _embed(text: str) -> list[float]:
    """A deterministic fake embedding, by the hashing trick.

    Each token is hashed to one of EMBEDDING_DIM buckets with a sign, the
    buckets are summed, and the result is normalised. The cosine similarity of
    two such vectors is therefore a signed measure of *shared vocabulary*.

    That is emphatically not what a real embedding model does - it has no
    notion that "cold" and "freezing" are related, and it never will. The
    pipeline does not use embeddings; this endpoint is here so that MODE=gateway
    can show the second half of the OpenAI protocol from the shell, offline.
    """
    vector = [0.0] * EMBEDDING_DIM
    for token in _TOKEN.findall(text.lower()):
        digest = hashlib.sha256(token.encode()).digest()
        bucket = int.from_bytes(digest[:4], "big") % EMBEDDING_DIM
        vector[bucket] += 1.0 if digest[4] % 2 == 0 else -1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


@app.post("/v1/embeddings")
async def embeddings(body: dict):
    inputs = body.get("input") or []
    if isinstance(inputs, str):
        inputs = [inputs]
    return {
        "object": "list",
        "model": body.get("model", "mock-embed"),
        "data": [{"object": "embedding", "index": i, "embedding": _embed(text)}
                 for i, text in enumerate(inputs)],
        "usage": {"prompt_tokens": sum(_tokens(t) for t in inputs),
                  "total_tokens": sum(_tokens(t) for t in inputs)},
    }


@app.get("/v1/models")
async def models():
    return {"object": "list",
            "data": [{"id": "mock-model", "object": "model", "owned_by": "agentlab"},
                     {"id": "mock-embed", "object": "model", "owned_by": "agentlab"}]}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
