"""Score a completed run: read output/items/, check it against eval/expectations.json.

    python -m app.evaluate               # every item that has an expectation
    python -m app.evaluate logo.png

A trace tells you what the pipeline did. It does not tell you whether that
was right. This module is the smallest thing that does, and it is
deliberately dumb: equality and substring checks over the files the pipeline
wrote. No model judges anything here, because a grader you cannot audit is
not a grader.

Three properties worth copying into your own project:

  1. **The answer key is not reachable by the agent.** `eval/` is mounted into
     this container and no other.
  2. **output/ is mounted read-only.** By the time a run is scored it is a
     fixed artefact. The evaluator cannot quietly repair what it is judging.
  3. **A missing expectation is reported, not skipped**, and so is a missing
     output. The commonest way for a suite to go green is to stop checking.

What an item's expectation may declare, all optional:

    status              "ok" (the default) or "error"
    kind                "text", "image" or "binary" - what classify decided
    result              key/value pairs the tool's result must contain
    summary_contains    substrings the specialist's sentence must contain
    summary_excludes    substrings it must not - the leak check

And one rule that applies to every item a specialist processed: it must have
called exactly the tool for its kind, and no other. An item answered from
memory made no calls, and is checked on its result alone.
"""

import json
import logging
import sys
from typing import Any

from app import config

log = logging.getLogger("eval")

TOOL_FOR = {"text": "process_text", "image": "process_image", "binary": "process_binary"}


def load_expectations() -> dict[str, dict[str, Any]]:
    path = config.EVAL_DIR / "expectations.json"
    if not path.is_file():
        raise SystemExit(f"no expectations at {path}")
    return json.loads(path.read_text())


def load_output(item_id: str) -> dict[str, Any] | None:
    path = config.OUTPUT_DIR / "items" / f"{item_id}.json"
    return json.loads(path.read_text()) if path.is_file() else None


def _tools_called(output: dict[str, Any]) -> list[str]:
    return [call["name"]
            for message in output.get("transcript", [])
            for call in message.get("tool_calls", []) or []]


def check(item_id: str, expected: dict[str, Any],
          output: dict[str, Any] | None) -> dict[str, Any]:
    if output is None:
        return {"id": item_id, "passed": False, "checks": 0,
                "failures": ["no output - was this item processed?"]}

    failures: list[str] = []
    checks = 0

    def want(condition: bool, message: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(message)

    status = expected.get("status", "ok")
    want(output.get("status") == status,
         f"status is {output.get('status')!r}, expected {status!r}"
         + (f" ({output.get('error')})" if output.get("error") else ""))

    if "kind" in expected:
        want(output.get("kind") == expected["kind"],
             f"classified as {output.get('kind')!r}, expected {expected['kind']!r}")

    result = output.get("result") or {}
    for key, value in expected.get("result", {}).items():
        want(result.get(key) == value,
             f"result[{key!r}] is {result.get(key)!r}, expected {value!r}")

    summary = str(output.get("summary") or "")
    for needle in expected.get("summary_contains", []):
        want(needle.lower() in summary.lower(), f"summary does not mention {needle!r}")
    for needle in expected.get("summary_excludes", []):
        want(needle.lower() not in summary.lower(), f"summary leaked {needle!r}")

    if output.get("source") == "specialist" and output.get("kind") in TOOL_FOR:
        called = _tools_called(output)
        tool = TOOL_FOR[output["kind"]]
        want(called == [tool],
             f"specialist called {called or 'nothing'}, expected exactly [{tool!r}]")

    return {"id": item_id, "passed": not failures, "failures": failures,
            "checks": checks, "source": output.get("source")}


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S")

    expectations = load_expectations()
    if argv:
        unknown = set(argv) - set(expectations)
        if unknown:
            raise SystemExit(f"no expectation for: {', '.join(sorted(unknown))}")
        expectations = {k: v for k, v in expectations.items() if k in argv}

    results = [check(item_id, expected, load_output(item_id))
               for item_id, expected in sorted(expectations.items())]

    # An output with no expectation is a hole in the suite: say so.
    items_dir = config.OUTPUT_DIR / "items"
    unscored = sorted(p.name.removesuffix(".json") for p in items_dir.glob("*.json")
                      if p.name.removesuffix(".json") not in expectations) \
        if items_dir.is_dir() and not argv else []

    print()
    print(f"{'item':<18} {'result':<7} {'source':<11} {'checks':>6}")
    print("-" * 46)
    for r in results:
        print(f"{r['id']:<18} {'pass' if r['passed'] else 'FAIL':<7} "
              f"{r.get('source') or '-':<11} {r['checks']:>6}")
        for failure in r["failures"]:
            print(f"    - {failure}")
    print()
    if unscored:
        print(f"  not scored (no expectation): {', '.join(unscored)}")
        print()

    passed = sum(1 for r in results if r["passed"])
    report = {"passed": passed, "failed": len(results) - passed,
              "unscored": unscored, "results": results}
    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (config.REPORT_DIR / "evaluation.json").write_text(json.dumps(report, indent=2) + "\n")

    print(f"  {passed}/{len(results)} items passed "
          f"-> {config.REPORT_DIR / 'evaluation.json'}")
    print()
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
