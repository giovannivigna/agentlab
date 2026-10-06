"""The three specialist agents: one per kind of item, one tool each.

Each specialist is a small ReAct agent - a model, a role prompt, and a single
tool - and the graph hands it one item at a time. What it is told is the
item's ID, kind and size. What it is never told is the item's *content*.

That is the design decision this file exists to show. The tool the model sees
takes no data argument (process_binary takes only the choice of hash). When
the model calls it, the wrapper below fetches the item's bytes from the
pipeline's blob store and forwards them to the real MCP tool, whose schema
does include the data. So:

  * the model decides *whether* and *how* to process the item - that is the
    agent part;
  * the bytes go from the I/O server to the processing server without ever
    entering a prompt - no tokens spent on base64, and no instructions hidden
    in a file get read by a model that might follow them.

The model's view of a tool and the server's view of the same tool differ on
purpose. Compare `args_schema` here with the signatures in mcp/server.py.
"""

import base64
import json
from typing import Literal

from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from mcp import Client
from pydantic import BaseModel, Field

from app import clients, config

PROMPTS = {
    "text": (
        "You are the text specialist in a processing pipeline. You are given "
        "one input item by ID. Call `process_text` exactly once to measure it, "
        "then state the result in one short sentence. You cannot see the "
        "item's content and do not need to."),
    "image": (
        "You are the image specialist in a processing pipeline. You are given "
        "one input item by ID. Call `process_image` exactly once to measure "
        "it, then state its format and size in one short sentence. You cannot "
        "see the image and do not need to."),
    "binary": (
        "You are the binary specialist in a processing pipeline. You are given "
        "one input item by ID. Call `process_binary` exactly once to "
        "fingerprint it - sha256 unless there is a reason to prefer another - "
        "then state the result in one short sentence. You cannot see the "
        "data and do not need to."),
}


class _NoArgs(BaseModel):
    """No arguments: the item is supplied by the pipeline."""


class _BinaryArgs(BaseModel):
    algorithm: Literal["sha256", "sha1", "md5"] = Field(
        default="sha256", description="Which hash to compute.")


def _tool(kind: str, remote: Client, data: bytes) -> StructuredTool:
    """The model-facing tool for one item, bound to that item's bytes."""
    if kind == "text":
        async def run() -> str:
            # Classification already proved this decodes as UTF-8.
            return json.dumps(await clients.call(
                remote, "process_text", {"text": data.decode("utf-8")}))
        return StructuredTool.from_function(
            coroutine=run, name="process_text", args_schema=_NoArgs,
            description="Count the characters in the current text item.")

    encoded = base64.b64encode(data).decode("ascii")
    if kind == "image":
        async def run() -> str:
            return json.dumps(await clients.call(
                remote, "process_image", {"data": encoded}))
        return StructuredTool.from_function(
            coroutine=run, name="process_image", args_schema=_NoArgs,
            description="Report the current image item's width, height and format.")

    async def run(algorithm: str = "sha256") -> str:
        return json.dumps(await clients.call(
            remote, "process_binary", {"data": encoded, "algorithm": algorithm}))
    return StructuredTool.from_function(
        coroutine=run, name="process_binary", args_schema=_BinaryArgs,
        description="Hash the current binary item.")


def _model() -> ChatOpenAI:
    return ChatOpenAI(
        model=config.MODEL,
        base_url=config.BASE_URL,      # the gateway, never a vendor
        api_key=config.API_KEY,
        temperature=0,                 # reproducible runs; raise it to see why
        timeout=60,
        max_retries=2,
    )


async def run(kind: str, item: dict, data: bytes, remote: Client,
              run_config: dict) -> dict:
    """Run the specialist for `kind` on one item.

    Returns {"result": the tool's answer, "summary": the model's sentence,
    "transcript": what was said}. A specialist is built per item because its
    tool is bound to that item's bytes; building one is a few objects, not a
    network call.
    """
    agent = create_react_agent(model=_model(), tools=[_tool(kind, remote, data)],
                               prompt=PROMPTS[kind], name=f"{kind}_specialist")
    question = (f"Process item `{item['id']}` ({kind}, {item['bytes']} bytes).")
    state = await agent.ainvoke(
        {"messages": [{"role": "user", "content": question}]},
        {**run_config, "recursion_limit": config.MAX_STEPS})

    messages = state["messages"]
    tool_results = [m for m in messages if m.__class__.__name__ == "ToolMessage"]
    result = None
    if tool_results:
        try:
            result = json.loads(tool_results[-1].content)
        except (TypeError, json.JSONDecodeError):
            result = {"text": str(tool_results[-1].content)}
    return {
        # None means the model never called its tool - which is worth seeing
        # in the output rather than papering over.
        "result": result,
        "summary": messages[-1].content,
        "transcript": [_record(m) for m in messages],
    }


def _record(message) -> dict:
    """A message, flattened to something a human can read in a diff."""
    record = {"type": message.__class__.__name__.replace("Message", "").lower(),
              "content": message.content}
    if getattr(message, "tool_calls", None):
        record["tool_calls"] = [{"name": c["name"], "args": c["args"]}
                                for c in message.tool_calls]
    if getattr(message, "name", None):
        record["tool"] = message.name
    return record
