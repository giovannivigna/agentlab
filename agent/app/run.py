"""The harness: connect the tool servers, run the pipeline once, report.

    python -m app.run                     # every item in input/items/
    python -m app.run logo.png hello.txt  # just these

The pipeline (app/graph.py) does the work and writes one output per item,
through the local MCP server. This file is the boring half around it, and the
half that decides whether anyone can trust a run:

  1. **Startup fails loudly.** No processing server, no I/O server, no
     knowledge base when the mode promises one: the run stops before the
     first item rather than three items in.
  2. **Every item is accounted for.** Each one ends up in output/items/,
     processed or with its error. run.json lists them all, with the trace.
  3. **The run is bounded.** AGENT_MAX_ITEMS caps the batch and
     AGENT_MAX_STEPS caps each specialist; the graph's recursion limit is
     derived from both.
"""

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone

from app import clients, config, graph, kb, tracing

log = logging.getLogger("agent")


def _root(exc: BaseException) -> BaseException:
    """The first real exception inside anyio's exception groups."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


async def _main(argv: list[str]) -> int:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpx2", "mcp", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    log.info("mode: pipeline + processing server%s",
             "".join(f" + {c}" for c in sorted(config.capabilities())))
    log.info("model: %s via %s", config.MODEL, config.BASE_URL)

    if config.KB_ENABLED:
        try:
            log.info("memory: %s", await kb.health())
        except Exception as exc:                    # noqa: BLE001
            log.error("knowledge base unreachable: %s", exc)
            return 2

    handler = tracing.callback_handler()
    log.info("tracing: %s", "on" if handler else "off")

    started = time.monotonic()
    trace_id = None
    try:
        async with clients.connect() as (local, remote):
            pipeline = graph.build(local, remote, set(argv) or None)
            run_config = {
                "recursion_limit": graph.recursion_limit(),
                "callbacks": [handler] if handler else [],
                "run_name": "pipeline",
                "tags": ["agentlab", "pipeline"],
                "metadata": {"langfuse_session_id": "pipeline"},
            }
            with tracing.task_trace("pipeline", "process input/items") as trace:
                state = await pipeline.ainvoke({"queue": [], "done": []}, run_config)
                trace_id = trace["trace_id"]
                tracing.record_output(trace, graph.summarise(state["done"]))
    except Exception as exc:                        # noqa: BLE001
        root = _root(exc)
        log.error("pipeline failed before finishing: %s: %s",
                  type(root).__name__, root)
        return 2
    finally:
        if config.KB_ENABLED:
            await kb.close()
        tracing.flush()

    done = state["done"]
    trace_url = tracing.trace_url(trace_id)
    run = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "seconds": round(time.monotonic() - started, 2),
        "model": config.MODEL,
        "capabilities": sorted(config.capabilities()),
        "trace_url": trace_url,
        "items": done,
    }
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (config.OUTPUT_DIR / "run.json").write_text(json.dumps(run, indent=2) + "\n")

    print()
    print(f"{'item':<18} {'kind':<8} {'source':<11} {'status':<7} summary")
    print("-" * 78)
    for d in done:
        print(f"{d['id']:<18} {d['kind'] or '-':<8} {d['source'] or '-':<11} "
              f"{d['status']:<7} {d['error'] or d['summary']}")
    print()
    if trace_url:
        print(f"  trace  {trace_url}")
    print(f"  written to {config.OUTPUT_DIR}/items/ and run.json")
    print()
    return 0 if all(d["status"] == "ok" for d in done) else 1


def main(argv: list[str]) -> int:
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
