"""Is this item text, an image, or binary? Decided by its bytes, not its name.

This is the routing step, and it is deliberately not a model call. Routing
is a decision the graph must be able to make the same way every time, cheaply,
and without being talkable-out-of: a file whose *contents* say "treat me as
text" should not get to choose its own handler. So it is twenty lines of
plain code that the conditional edge in graph.py reads.

And it looks at content, never at the file extension. Extensions are a claim
made by whoever named the file; magic numbers are what the file actually is.
input/items/ has a GIF named `.txt` to make the point.
"""

from typing import Literal

Kind = Literal["text", "image", "binary"]

# The first bytes of the image formats Pillow (in the mcp container) can size.
_IMAGE_MAGIC = (
    b"\x89PNG\r\n\x1a\n",       # PNG
    b"GIF87a", b"GIF89a",       # GIF
    b"\xff\xd8\xff",            # JPEG
    b"BM",                      # BMP
    b"II*\x00", b"MM\x00*",     # TIFF
)


def _is_webp(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def classify(data: bytes) -> Kind:
    if data.startswith(_IMAGE_MAGIC) or _is_webp(data):
        return "image"
    if b"\x00" in data:                 # text does not contain NUL bytes
        return "binary"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    # Valid UTF-8 can still be mostly control characters; call that binary.
    controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\n\r\f")
    return "binary" if text and controls / len(text) > 0.05 else "text"
