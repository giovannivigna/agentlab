"""The pipeline: a LangGraph state machine that processes input/ one item at a time.

    START -> list_inputs -> read_next --(none left)--> END
                               |   ^
                     (unreadable)  |
                               |   +------------------------------- write
                               v                                      ^
                            classify -> recall --(remembered)---------+
                                           |                          |
                         (by kind)  text_agent | image_agent | binary_agent
                                           |                          |
                                           +--------> remember -------+

Who decides what, which is the point of drawing it:

  * the GRAPH decides the order, the routing and the output ID. classify is
    plain code over magic numbers; the conditional edges read its answer;
    write_output always uses the input's own ID. None of that is negotiable
    by a model or by the content of a file.
  * the SPECIALISTS decide how to process one item - an LLM agent each, with
    one tool (app/specialists.py).
  * the MCP SERVERS do the work: the local one (stdio) reads and writes, the
    remote one (HTTP, in its own container) measures and hashes.

The item's bytes are not in the graph state. State carries the item's ID,
size, hash and kind; the bytes sit in `blobs` below, one item at a time. That
keeps base64 out of every checkpoint and every trace, and it is the same rule
the specialists follow: pass references, not payloads.
"""

import base64
import hashlib
import json
import logging
import operator
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from mcp import Client

from app import classify as classifier
from app import clients, kb, specialists
from app import config as settings

log = logging.getLogger(__name__)

AGENTS = {"text": "text_agent", "image": "image_agent", "binary": "binary_agent"}

# Graph steps per item: read_next, classify, recall, agent, remember, write.
STEPS_PER_ITEM = 6


class Item(TypedDict, total=False):
    id: str
    bytes: int
    sha256: str
    kind: str


class State(TypedDict, total=False):
    queue: list[str]                 # IDs still to process
    item: Item | None                # the one in hand
    result: dict | None              # what the tool found
    summary: str | None              # what the specialist said about it
    source: str | None               # "specialist" | "memory" | "error"
    error: str | None
    transcript: list[dict]           # the specialist's conversation
    done: Annotated[list[dict], operator.add]   # one line per finished item


def recursion_limit() -> int:
    """The graph-level bound, derived from the item cap - see config.MAX_ITEMS."""
    return STEPS_PER_ITEM * settings.MAX_ITEMS + 10


def build(local: Client, remote: Client, only: set[str] | None = None):
    blobs: dict[str, bytes] = {}

    # --- I/O, through the local MCP server --------------------------------

    async def list_inputs(state: State) -> dict:
        ids = (await clients.call(local, "list_inputs", {}))["ids"]
        if only:
            missing = only - set(ids)
            if missing:
                log.warning("no such input: %s", ", ".join(sorted(missing)))
            ids = [i for i in ids if i in only]
        if len(ids) > settings.MAX_ITEMS:
            log.warning("%d items; processing the first %d (AGENT_MAX_ITEMS)",
                        len(ids), settings.MAX_ITEMS)
            ids = ids[:settings.MAX_ITEMS]
        log.info("inputs: %s", ", ".join(ids) or "none")
        return {"queue": ids}

    async def read_next(state: State) -> dict:
        blobs.clear()                    # one item's bytes in memory at a time
        queue = state.get("queue") or []
        if not queue:
            return {"item": None}
        item_id, rest = queue[0], queue[1:]
        fresh = {"queue": rest, "result": None, "summary": None, "source": None,
                 "error": None, "transcript": []}
        reply = await clients.call(local, "read_input", {"id": item_id})
        if "error" in reply:
            return {**fresh, "source": "error", "error": reply["error"],
                    "item": {"id": item_id, "bytes": 0, "sha256": "", "kind": "unreadable"}}
        data = base64.b64decode(reply["data"])
        blobs[item_id] = data
        return {**fresh, "item": {"id": item_id, "bytes": len(data),
                                  "sha256": hashlib.sha256(data).hexdigest()}}

    async def write(state: State) -> dict:
        item, result = state["item"], state.get("result")
        error = state.get("error") or (result or {}).get("error")
        record = {
            "id": item["id"],
            "kind": item.get("kind"),
            "bytes": item.get("bytes"),
            "sha256": item.get("sha256"),
            "source": state.get("source"),
            "status": "error" if error else "ok",
            "result": result,
            "summary": state.get("summary"),
            "error": error,
            "model": settings.MODEL if state.get("source") == "specialist" else None,
            "transcript": state.get("transcript") or [],
        }
        reply = await clients.call(local, "write_output",
                                   {"id": item["id"], "content": json.dumps(record, indent=2)})
        if "error" in reply:
            record.update(status="error", error=f"write failed: {reply['error']}")
        log.info("item %-16s %-7s %-10s %s", item["id"], item.get("kind"),
                 record["source"], record["error"] or record["summary"])
        return {"done": [{k: record[k] for k in
                          ("id", "kind", "source", "status", "summary", "error")}]}

    # --- routing: plain code, not a model ---------------------------------

    def classify(state: State) -> dict:
        item = state["item"]
        return {"item": {**item, "kind": classifier.classify(blobs[item["id"]])}}

    # --- memory, when there is a knowledge base ---------------------------

    async def recall(state: State) -> dict:
        if not settings.KB_ENABLED:
            return {}
        item = state["item"]
        hit = await kb.recall(item["sha256"])
        if hit is None or hit["kind"] != item["kind"]:
            return {}
        return {"result": hit["result"], "summary": hit["summary"], "source": "memory"}

    async def remember(state: State) -> dict:
        result = state.get("result")
        if (settings.KB_ENABLED and state.get("source") == "specialist"
                and result and "error" not in result):
            item = state["item"]
            await kb.remember(item["sha256"], item["kind"], result,
                              state.get("summary") or "", item["id"])
        return {}

    # --- the specialists --------------------------------------------------

    def specialist(kind: str):
        # `config` is LangGraph's per-call config - callbacks included - and
        # passing it on is what puts the specialist's trace inside this one.
        async def node(state: State, config: RunnableConfig) -> dict:
            item = state["item"]
            try:
                out = await specialists.run(kind, item, blobs[item["id"]],
                                            remote, config)
            except Exception as exc:              # noqa: BLE001
                # One item failing must not take the batch down, and must not
                # vanish: it is written out with its error, like any other.
                # One line here; the traceback only at AGENT_LOG_LEVEL=DEBUG.
                log.error("item %s: %s specialist failed: %s: %s", item["id"], kind,
                          type(exc).__name__, str(exc)[:160])
                log.debug("traceback", exc_info=True)
                return {"source": "error", "error": f"{type(exc).__name__}: {exc}"}
            return {"result": out["result"], "summary": out["summary"],
                    "transcript": out["transcript"], "source": "specialist",
                    "error": None if out["result"] is not None
                    else "the specialist never called its tool"}
        node.__name__ = AGENTS[kind]
        return node

    # --- the wiring -------------------------------------------------------

    def after_read(state: State) -> str:
        if state.get("item") is None:
            return END
        return "write" if state.get("source") == "error" else "classify"

    def after_recall(state: State) -> str:
        return "write" if state.get("source") == "memory" \
            else AGENTS[state["item"]["kind"]]

    graph = StateGraph(State)
    graph.add_node("list_inputs", list_inputs)
    graph.add_node("read_next", read_next)
    graph.add_node("classify", classify)
    graph.add_node("recall", recall)
    for kind, name in AGENTS.items():
        graph.add_node(name, specialist(kind))
    graph.add_node("remember", remember)
    graph.add_node("write", write)

    graph.add_edge(START, "list_inputs")
    graph.add_edge("list_inputs", "read_next")
    graph.add_conditional_edges("read_next", after_read, [END, "write", "classify"])
    graph.add_edge("classify", "recall")
    graph.add_conditional_edges("recall", after_recall, ["write", *AGENTS.values()])
    for name in AGENTS.values():
        graph.add_edge(name, "remember")
    graph.add_edge("remember", "write")
    graph.add_edge("write", "read_next")
    return graph.compile(name="pipeline")


def summarise(done: list[dict[str, Any]]) -> str:
    """One line per item, for the trace's output and the terminal."""
    return "\n".join(f"{d['id']}: {d['error'] or d['summary']}" for d in done)
