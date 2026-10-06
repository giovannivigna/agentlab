"""Tracing: the run's own account of itself.

Langfuse receives one trace per run, containing every model call, every tool
call, the arguments, the results, latencies and token counts. That object
has a name: it is the evidence. "The agent deleted the file" and
"the agent was persuaded to delete the file by text it read in an input
document" are the same outcome and different incidents, and only the trace
tells you which one happened.

Every function here is wired to fail *soft*. If Langfuse is down, or the `obs`
profile is switched off, or the SDK renamed something between releases, the
agent still runs and still writes output - it logs `tracing: off` and carries
on. An observability stack that can take the system down with it is not
observability, and that is worth a few `except Exception` blocks that would be
sloppy anywhere else in this codebase.
"""

import logging
from contextlib import contextmanager

from app import config

log = logging.getLogger(__name__)

TAGS = ["agentlab", "setup-demo"]

_client = None
_checked = False


def enabled() -> bool:
    return bool(config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY
                and config.LANGFUSE_BASE_URL)


def client():
    """The Langfuse client, or None if tracing is off or unavailable.

    `get_client()` reads LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY /
    LANGFUSE_BASE_URL from the environment - which compose.yaml has already
    put there - and returns the same singleton the CallbackHandler will use.
    Constructing our own client instead would risk the handler and the span
    below ending up on two different ones.
    """
    global _client, _checked
    if _checked:
        return _client
    _checked = True
    if not enabled():
        return None
    try:
        from langfuse import get_client

        candidate = get_client()
        if not candidate.auth_check():
            log.warning("langfuse: credentials rejected; running untraced")
            return None
        _client = candidate
    except Exception as exc:                       # noqa: BLE001 - see above
        log.warning("langfuse: unavailable (%s); running untraced", exc)
    return _client


def callback_handler():
    """The LangChain callback that turns graph execution into a trace.

    Returns None when tracing is off, which every caller must handle - hence
    `callbacks=[h] if h else []` at the one call site in run.py.
    """
    if client() is None:
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler()
    except Exception as exc:                       # noqa: BLE001
        log.warning("langfuse: no callback handler (%s); running untraced", exc)
        return None


@contextmanager
def task_trace(name: str, question: str):
    """One trace for one unit of work. Yields a dict that ends up holding its id.

    The pipeline opens one per run (app/run.py): every item's steps,
    specialists and tool calls nest inside it, under spans named after the
    graph's nodes, so "what happened to logo.png" is one subtree to expand.
    """
    box: dict[str, object] = {"trace_id": None, "span": None}
    active = client()
    manager = None

    if active is not None:
        try:
            manager = _open(active, f"task:{name}", question)
            span = manager.__enter__()
            box["span"] = span
            # get_current_trace_id() is authoritative: the span object's own
            # id attribute has moved around between SDK versions, this has not.
            box["trace_id"] = (active.get_current_trace_id()
                               or getattr(span, "trace_id", None))
            # name and input were passed to _open; tags live on the trace, and
            # not every SDK version exposes a way to set them from a span.
            _try(span, "update_trace", tags=TAGS)
        except Exception as exc:                   # noqa: BLE001
            log.warning("langfuse: could not open a trace (%s)", exc)
            manager = None
    try:
        yield box
    finally:
        if manager is not None:
            try:
                manager.__exit__(None, None, None)
            except Exception as exc:               # noqa: BLE001
                log.debug("langfuse: closing the trace failed (%s)", exc)


def _open(active, name: str, question: str):
    """Start a trace-root span, whatever this SDK version calls that.

    Langfuse 4.x renamed `start_as_current_span` to
    `start_as_current_observation(as_type=...)`. Both spellings are tried
    rather than pinned, because the cost of guessing wrong is a silent loss of
    the one artefact you most need after
    something has gone wrong.
    """
    if hasattr(active, "start_as_current_observation"):
        return active.start_as_current_observation(
            name=name, as_type="agent", input=question)
    return active.start_as_current_span(name=name)


def record_output(box: dict, output) -> None:
    """Put the final answer on the trace, so a list of traces is readable."""
    span = box.get("span")
    if span is not None:
        _try(span, "update", output=output)
        _try(span, "update_trace", output=output)


def flush() -> None:
    """Send whatever is still queued.

    The SDK batches in a background thread. A short-lived process that exits
    without flushing loses the tail of its own trace - which is, reliably, the
    part you wanted to look at.
    """
    if _client is not None:
        _try(_client, "flush")


def trace_url(trace_id) -> str | None:
    """A link a human can open, built for the host's browser, not the container."""
    if not trace_id:
        return None
    base = config.LANGFUSE_PUBLIC_URL.rstrip("/")
    if config.LANGFUSE_PROJECT_ID:
        return f"{base}/project/{config.LANGFUSE_PROJECT_ID}/traces/{trace_id}"
    return f"{base}/trace/{trace_id}"


def _try(obj, method: str, **kwargs) -> None:
    """Call `obj.method(**kwargs)` if it exists, and swallow whatever it does.

    Taking the method by *name* rather than as a bound callable is the whole
    point. `_try(span.update_trace, ...)` looks equivalent and is not: the
    attribute is resolved before the guard is entered, so a method this SDK
    version does not have raises AttributeError straight past it. That is not
    hypothetical - it is what happened here when Langfuse v4 renamed things,
    and it turned "the trace is missing a field" into "the task failed".
    """
    func = getattr(obj, method, None)
    if func is None:
        log.debug("langfuse: no %s on %s", method, type(obj).__name__)
        return
    try:
        func(**kwargs)
    except Exception as exc:                       # noqa: BLE001
        log.debug("langfuse: %s failed (%s)", method, exc)
