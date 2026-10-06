"""The two MCP connections the pipeline holds for a run.

    local   app/io_server.py, launched as a subprocess, over stdio
    remote  the processing server at MCP_URL, over Streamable HTTP

Same protocol, same client class, two transports - which is the point of a
protocol. The pipeline does not care where a tool runs; compose.yaml and
this file do.
"""

import json
import logging
import os
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from mcp import Client, StdioServerParameters

from app import config

log = logging.getLogger(__name__)

PROCESSORS = ("process_text", "process_image", "process_binary")


class ToolError(RuntimeError):
    """A tool answered, and the answer was an error."""


async def call(client: Client, tool: str, args: dict) -> dict:
    """Call an MCP tool and return its result as a dict.

    MCPServer sends a dict result as JSON text content (and sometimes as
    structured content too); accept either.
    """
    result = await client.call_tool(tool, args)
    if result.structured_content is not None and isinstance(result.structured_content, dict):
        payload = result.structured_content
    else:
        text = "\n".join(getattr(block, "text", "") for block in result.content)
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError:
            payload = {"text": text}
    if result.is_error:
        raise ToolError(f"{tool}: {payload}")
    return payload


@asynccontextmanager
async def connect():
    """Yield (local, remote) clients, open for the whole run.

    Fails loudly if either server is missing a tool the pipeline needs: a
    run that discovers halfway through that it cannot write is worse than
    one that never starts.
    """
    local_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.io_server"],
        cwd=str(Path(__file__).resolve().parent.parent),
        # Explicitly, and only, what the I/O server needs. The SDK adds PATH
        # and little else; the gateway key and the KB password stay here.
        env={"INPUT_DIR": str(config.INPUT_DIR),
             "OUTPUT_DIR": str(config.OUTPUT_DIR),
             "PYTHONPATH": os.environ.get("PYTHONPATH", "")},
    )
    async with AsyncExitStack() as stack:
        local = await stack.enter_async_context(
            Client(local_params, read_timeout_seconds=30))
        remote = await stack.enter_async_context(
            Client(config.MCP_URL, read_timeout_seconds=60))

        for name, client, needed in (
                ("local io", local, ("list_inputs", "read_input", "write_output")),
                (config.MCP_URL, remote, PROCESSORS)):
            offered = {t.name for t in (await client.list_tools()).tools}
            missing = set(needed) - offered
            if missing:
                raise RuntimeError(f"{name} does not offer {sorted(missing)}")
            log.info("mcp: %s offers %s", name, ", ".join(sorted(offered)))
        yield local, remote
