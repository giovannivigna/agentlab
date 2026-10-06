"""Sync gateway/agents.yaml into the gateway as LiteLLM virtual keys.

Runs as the one-shot `gateway-keys` service, from the gateway's own image
(which already has Python and PyYAML), and is the only process besides the
gateway itself that holds the master key. It talks to the gateway's admin API:

    /key/info      does this key exist?
    /key/generate  create it, with the limits from agents.yaml
    /key/update    change the limits of an existing key in place
    /key/delete    revoke a key whose entry was removed from agents.yaml

Every key it creates is tagged `managed_by: agents.yaml`, and only keys with
that tag are ever revoked - a key you made by hand in the LiteLLM UI is left
alone.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml

GATEWAY = os.environ.get("GATEWAY_URL", "http://gateway:4000").rstrip("/")
MASTER_KEY = os.environ["LITELLM_MASTER_KEY"]
AGENTS_FILE = os.environ.get("AGENTS_FILE", "/provision/agents.yaml")
TAG = {"managed_by": "agents.yaml"}


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        GATEWAY + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {MASTER_KEY}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except ValueError:
            return exc.code, {}


def resolve(value: str) -> str:
    """`os.environ/NAME` -> the value of NAME, as in config.yaml."""
    if isinstance(value, str) and value.startswith("os.environ/"):
        name = value.removeprefix("os.environ/")
        if not os.environ.get(name):
            sys.exit(f"gateway-keys: {name} is not set - add it to .env and "
                     f"to gateway-keys' environment in compose.yaml")
        return os.environ[name]
    return value


def sync(name: str, spec: dict) -> None:
    spec = dict(spec)
    key = resolve(spec.pop("key", None))
    # LiteLLM's own rules for a virtual key, checked here so the error names
    # the agent rather than arriving as a 400 from the gateway.
    if not key or not key.startswith("sk-") or len(key) < 16:
        sys.exit(f"gateway-keys: {name}: `key` must start with 'sk-' and be "
                 f"at least 16 characters")

    body = {**spec, "key": key, "key_alias": name,
            "metadata": {**spec.get("metadata", {}), **TAG}}

    status, _ = call("GET", "/key/info?" + urllib.parse.urlencode({"key": key}))
    verb, path = ("updated", "/key/update") if status == 200 \
        else ("created", "/key/generate")
    status, reply = call("POST", path, body)
    if status != 200:
        sys.exit(f"gateway-keys: {name}: {path} failed ({status}): {reply}")

    limits = ", ".join(f"{k}={v}" for k, v in spec.items() if k != "metadata")
    print(f"  {verb:8} {name:14} {limits}")


def revoke_removed(wanted: set[str]) -> None:
    keys, page = [], 1
    while True:                              # the gateway pages at 100 at most
        status, reply = call("GET", "/key/list?return_full_object=true"
                                    f"&size=100&page={page}")
        if status != 200:
            print(f"  warning: could not list keys ({status}); "
                  f"removed agents were not revoked")
            return
        batch = reply.get("keys", [])
        keys += batch
        if len(batch) < 100:
            break
        page += 1
    stale = [k for k in keys
             if isinstance(k, dict)
             and (k.get("metadata") or {}).get("managed_by") == TAG["managed_by"]
             and k.get("key_alias") not in wanted]
    for k in stale:
        status, reply = call("POST", "/key/delete", {"keys": [k["token"]]})
        if status == 200:
            print(f"  revoked  {k.get('key_alias')}")
        else:
            print(f"  warning: could not revoke {k.get('key_alias')} "
                  f"({status}): {reply}")


def main() -> None:
    with open(AGENTS_FILE) as f:
        agents = (yaml.safe_load(f) or {}).get("agents") or {}
    print(f"gateway-keys: syncing {len(agents)} agent(s) from agents.yaml")
    for name, spec in agents.items():
        sync(name, spec or {})
    revoke_removed(set(agents))


if __name__ == "__main__":
    main()
