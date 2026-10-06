"""The local MCP server: the agent's only door to input/ and output/.

    python -m app.io_server        # speaks JSON-RPC on stdin/stdout

The pipeline launches this as a subprocess and talks MCP to it over stdio -
the transport MCP was designed around - instead of opening files itself. It offers exactly
three operations, all by ID:

    list_inputs()               the IDs of the items waiting in input/items/
    read_input(id)              one item's bytes, base64-encoded
    write_output(id, content)   write the result for one ID to output/items/

An ID is a file name and nothing more: letters, digits, `.`, `_`, `-`, no
slashes, no leading dot. It is checked by pattern *and* by resolving the path
and confirming it is still inside the directory, because the pattern is the
check you think you need and the resolve is the one that catches what you did
not think of (a symlink in input/ pointing at /etc, say).

What this is, and is not. It is a narrow interface: the graph cannot
accidentally `open()` the wrong path, and every read and write appears as an
MCP call in the trace. It is NOT isolation. It runs in the agent's container,
as the agent's user, with the agent's mounts - the read-only `input/` mount
and the non-root user are what actually hold the line. The processing server
in mcp/ is the isolated one; compare the two in compose.yaml.
"""

import base64
import os
import re
from pathlib import Path

from mcp.server.mcpserver import MCPServer

# Only these two variables reach this process: the client passes them
# explicitly (app/clients.py) and the MCP SDK gives a stdio server nothing
# else beyond PATH. In particular, it never sees the agent's gateway key.
ITEMS_IN = Path(os.environ.get("INPUT_DIR", "/work/input")) / "items"
ITEMS_OUT = Path(os.environ.get("OUTPUT_DIR", "/work/output")) / "items"

ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_READ = 8 * 1024 * 1024       # what one read may pull into the pipeline
MAX_WRITE = 1024 * 1024          # what one result may put on disk

server = MCPServer(name="agentlab-io", version="1.0.0")


def _resolve(root: Path, item_id: str) -> Path:
    """`root / item_id`, or ValueError if the ID is malformed or escapes."""
    if not ID.match(item_id):
        raise ValueError(f"invalid id {item_id!r}: letters, digits, '.', '_' "
                         f"and '-' only, not starting with '.'")
    root = root.resolve()
    path = (root / item_id).resolve()
    if path.parent != root:
        raise ValueError(f"id {item_id!r} escapes {root}")
    return path


@server.tool()
def list_inputs() -> dict:
    """List the IDs of the input items, in a stable order."""
    if not ITEMS_IN.is_dir():
        return {"ids": [], "error": f"no input directory at {ITEMS_IN}"}
    ids = sorted(p.name for p in ITEMS_IN.iterdir()
                 if p.is_file() and ID.match(p.name))
    return {"ids": ids}


@server.tool()
def read_input(id: str) -> dict:
    """Read one input item by ID. Returns its bytes base64-encoded.

    Args:
        id: An ID from list_inputs, e.g. 'hello.txt'.
    """
    try:
        path = _resolve(ITEMS_IN, id)
    except ValueError as exc:
        return {"id": id, "error": str(exc)}
    if not path.is_file():
        return {"id": id, "error": "no such item"}
    size = path.stat().st_size
    if size > MAX_READ:
        return {"id": id, "error": f"item is {size} bytes; the limit is {MAX_READ}"}
    return {"id": id, "bytes": size,
            "data": base64.b64encode(path.read_bytes()).decode("ascii")}


@server.tool()
def write_output(id: str, content: str) -> dict:
    """Write the output for one ID, replacing any earlier one.

    Args:
        id: The ID the output belongs to - the input item's ID.
        content: The output document, as text (the pipeline writes JSON).
    """
    try:
        path = _resolve(ITEMS_OUT, id)
    except ValueError as exc:
        return {"id": id, "error": str(exc)}
    path = path.with_name(path.name + ".json")
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE:
        return {"id": id, "error": f"output is {len(data)} bytes; the limit is {MAX_WRITE}"}
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write then rename, so a crash never leaves half a result for eval to read.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return {"id": id, "path": f"output/items/{path.name}", "bytes": len(data)}


if __name__ == "__main__":
    server.run("stdio")
