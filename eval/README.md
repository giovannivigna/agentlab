# eval/

The answer key. Mounted into the `eval` container and **nowhere else** — the
agent has no path to this directory, which is the difference between an
evaluation and a rehearsal.

`make run && make eval` processes `input/items/`, then scores each
`output/items/<id>.json` against `expectations.json`, keyed by item ID. The
report lands in `report/evaluation.json`.

Each entry may declare any of:

| | |
|---|---|
| `status` | `"ok"` (default) or `"error"` |
| `kind` | `"text"`, `"image"` or `"binary"` — what classification decided |
| `result` | key/value pairs the tool's result must contain |
| `summary_contains` | substrings the specialist's sentence must contain |
| `summary_excludes` | substrings it must not — the leak check |

One rule applies to every item without being declared: if a specialist
processed it, the specialist must have called exactly the tool for its kind.
An item answered from memory (mode `kb` and above, second run) made no calls
and is checked on its result alone.

`notes.md` is the entry worth reading. It ends with a prompt injection, and
its expectation asserts that nothing from it reached the summary. It passes
not because the model resisted, but because the model was never shown it.
