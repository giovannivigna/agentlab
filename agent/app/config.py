"""Every knob, read once, in one place.

The rule this file exists to enforce: configuration comes from the
environment, and the environment comes from compose.yaml, which comes from
.env. Nothing in the agent reads a config file of its own, and nothing has a
hard-coded hostname. That is what lets the same image run against the mock
model while you build and a real one when you evaluate, without a rebuild.
"""

import os
from pathlib import Path

# --- the model -------------------------------------------------------------
# Note that there is no vendor name anywhere here. The agent knows a URL, a
# key and a model name; the gateway knows what those mean.
BASE_URL = os.environ.get("AGENT_BASE_URL", "http://gateway:4000/v1").rstrip("/")
API_KEY = os.environ.get("AGENT_API_KEY", "sk-agentlab-pipeline-local")
MODEL = os.environ.get("AGENT_MODEL", "mock-model")

# Two bounds, because there are two loops. MAX_STEPS caps one specialist's
# ReAct loop; MAX_ITEMS caps how many items one run will take from input/.
# Together they bound the run: an agent without them is an unbounded loop
# with a credit card.
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "12"))
MAX_ITEMS = int(os.environ.get("AGENT_MAX_ITEMS", "100"))

LOG_LEVEL = os.environ.get("AGENT_LOG_LEVEL", "INFO").upper()

# --- the tool servers ------------------------------------------------------
# Remote: the processing server, in its own container, over Streamable HTTP.
MCP_URL = os.environ.get("MCP_URL", "http://mcp:8000/mcp").strip()
# Local: the I/O server, a subprocess of this one, over stdio (app/io_server.py).


# --- which optional parts of the platform exist ----------------------------
# The stack runs in four modes (MODE in .env), and the agent is told which
# parts exist the only way it is told anything: by its environment.
def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


KB_ENABLED = _flag("KB_ENABLED")

# --- the knowledge base: the pipeline's memory -----------------------------
KB_DSN = os.environ.get(
    "KB_DSN", "postgresql://agentlab:agentlab-password@kb:5432/kb")

# --- tracing ---------------------------------------------------------------
LANGFUSE_BASE_URL = os.environ.get("LANGFUSE_BASE_URL", "")
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_PROJECT_ID = os.environ.get("LANGFUSE_PROJECT_ID", "")
# The URL a human can click, which is not the one the container talks to.
LANGFUSE_PUBLIC_URL = os.environ.get("LANGFUSE_PUBLIC_URL", "http://localhost:3000")

# --- the workspace ---------------------------------------------------------
# INPUT_DIR is mounted read-only by compose; OUTPUT_DIR is the only place a
# run leaves a mark. The pipeline itself touches neither directly: it goes
# through the local I/O server, which reads input/items/ and writes
# output/items/. The harness writes one file of its own, output/run.json.
INPUT_DIR = Path(os.environ.get("INPUT_DIR", "/work/input"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/work/output"))

# Read by app/evaluate.py and app/seed.py only. The agent container mounts
# neither eval/ nor kb/: an agent that can read the answer key is not being
# evaluated.
EVAL_DIR = Path(os.environ.get("EVAL_DIR", "/work/eval"))
REPORT_DIR = Path(os.environ.get("REPORT_DIR", "/work/report"))
SEED_DIR = Path(os.environ.get("SEED_DIR", "/work/kb/seed"))


def capabilities() -> set[str]:
    """The optional parts this run has, for the log and run.json."""
    return ({"kb"} if KB_ENABLED else set()) \
        | ({"tracing"} if LANGFUSE_BASE_URL else set())
