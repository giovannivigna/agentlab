# input/

Mounted into the agent container **read-only** at `/work/input`.

- `items/` — the items the pipeline processes, one file each. An item's
  **ID is its file name**: letters, digits, `.`, `_` and `-`, not starting
  with a dot. The pipeline never opens these files itself; it lists and reads
  them through the local MCP server (`agent/app/io_server.py`) by ID.

What is here, and why:

| ID | Is | Shows |
|---|---|---|
| `hello.txt` | UTF-8 text | characters (72) are not bytes (81) |
| `notes.md` | text, ending in a prompt injection | the specialist never sees content, so the injection has nothing to talk to |
| `logo.png` | a 64×32 PNG | the image specialist |
| `disguised.txt` | a 12×9 **GIF** with a `.txt` name | classification reads magic numbers, not extensions |
| `random.bin` | 512 bytes with NULs | the binary specialist |

Everything under this directory is **untrusted input**. Add your own files to
`items/` and `make run`; add an entry to `eval/expectations.json` or `make
eval` will tell you the item is unscored.
