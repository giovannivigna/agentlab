"""The processing server: three tools behind MCP, in a container of their own.

The agent's specialists call these over Streamable HTTP at
http://mcp:8000/mcp. Each tool receives an item's content and returns a fact
about it:

    process_text(text)                 how many characters
    process_image(data)                width, height and format
    process_binary(data, algorithm)    a hash of the bytes

Why is this a separate container rather than three functions in the agent?
Because two of these tools *parse untrusted input*. An image decoder is one of
the classic places a malicious file turns into code execution, and every item
in input/ is untrusted. So the parsing happens here, in a process that has no
mounts, a read-only filesystem, no Linux capabilities, no route to the
internet and no credentials of any kind - see `mcp:` in compose.yaml. If a
crafted PNG ever owns Pillow, it owns a box with nothing in it.

Bytes cross the wire as base64 inside the JSON-RPC call. They never pass
through a language model: the agent's specialist decides *to* call a tool, and
the graph supplies the bytes (agent/app/specialists.py).
"""

import base64
import binascii
import hashlib
import io
from typing import Literal

from mcp.server.mcpserver import MCPServer
from PIL import Image, UnidentifiedImageError
from starlette.requests import Request
from starlette.responses import JSONResponse

# In mcp 2.x this class is MCPServer; it was called FastMCP in 1.x.
server = MCPServer(name="agentlab-processors", version="2.0.0")

# A cap on what one call may ask this server to hold in memory. Inputs are
# untrusted; so is their size.
MAX_BYTES = 8 * 1024 * 1024


def _decode(data: str) -> bytes | dict:
    """base64 -> bytes, or an error dict the caller can return as-is."""
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        return {"error": f"data is not valid base64: {exc}"}
    if len(raw) > MAX_BYTES:
        return {"error": f"item is {len(raw)} bytes; the limit is {MAX_BYTES}"}
    return raw


@server.tool()
def process_text(text: str) -> dict:
    """Count the characters in a piece of text.

    Characters, not bytes: 'café' is 4 characters and 5 bytes of UTF-8.

    Args:
        text: The text to measure.
    """
    return {"characters": len(text),
            "bytes": len(text.encode("utf-8")),
            "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0)}


@server.tool()
def process_image(data: str) -> dict:
    """Report an image's size in pixels, and its format.

    Args:
        data: The image file's bytes, base64-encoded.
    """
    raw = _decode(data)
    if isinstance(raw, dict):
        return raw
    try:
        # `open` reads the header only, which is all the size needs - and is
        # why a decompression bomb costs nothing here until someone calls
        # .load(). Pillow's MAX_IMAGE_PIXELS guard backs that up.
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            return {"width": width, "height": height,
                    "format": image.format, "mode": image.mode,
                    "bytes": len(raw)}
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        return {"error": f"not a readable image: {exc}", "bytes": len(raw)}


@server.tool()
def process_binary(data: str,
                   algorithm: Literal["sha256", "sha1", "md5"] = "sha256") -> dict:
    """Hash a block of binary data.

    Args:
        data: The bytes to hash, base64-encoded.
        algorithm: Which hash. Defaults to sha256; md5 and sha1 are offered
            for matching older systems, not for anything that must resist
            tampering.
    """
    raw = _decode(data)
    if isinstance(raw, dict):
        return raw
    return {"algorithm": algorithm,
            "hash": hashlib.new(algorithm, raw).hexdigest(),
            "bytes": len(raw)}


@server.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """For the compose healthcheck. Not part of MCP, and not an MCP tool."""
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    # 0.0.0.0 because the agent reaches it as `mcp`, from another container.
    # Its exposure is set by compose.yaml: `internal` only, no published port.
    server.run("streamable-http", host="0.0.0.0", port=8000)
