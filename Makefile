# Agent Lab: a small, complete runtime for agentic pipelines.
# `make help` lists everything.
.DEFAULT_GOAL := help
SHELL := /bin/bash
DC := docker compose

.PHONY: help up down run eval shell logs ps seed keys kb ask clean check mode tour \
        need-agent need-kb

# ---------------------------------------------------------------------------
# MODE: which shape of the platform to run. Cumulative, by name or number:
#
#   1 gateway   the model gateway alone
#   2 agent     + the pipeline agent and its processing MCP server
#   3 kb        + the knowledge base, as the pipeline's memory
#   4 full      + Langfuse tracing
#
# Read from .env, overridable per command: `make up MODE=kb`. Everything
# below is derived from it, so there is exactly one switch.
# ---------------------------------------------------------------------------
MODES := gateway agent kb full
MODE ?= $(or $(shell sed -n 's/^MODE=//p' .env 2>/dev/null | tail -1),full)
override MODE := $(if $(filter 1 2 3 4,$(MODE)),$(word $(MODE),$(MODES)),$(MODE))
ifeq ($(filter $(MODE),$(MODES)),)
$(error MODE=$(MODE) is not one of: $(MODES) (or 1-4))
endif

PROFILES_gateway :=
PROFILES_agent   := mcp
PROFILES_kb      := mcp,kb
PROFILES_full    := mcp,kb,obs

# Which services compose starts, and what the agent is told exists.
export MODE
export COMPOSE_PROFILES  := $(PROFILES_$(MODE))
export KB_ENABLED        := $(if $(filter kb full,$(MODE)),true,false)
export LANGFUSE_BASE_URL := $(if $(filter full,$(MODE)),http://langfuse:3000,)

HAS_KB  := $(filter kb full,$(MODE))
HAS_MCP := $(filter agent kb full,$(MODE))

help:  ## show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk -F':.*?## ' '{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  MODE=$(MODE)   (set in .env, or per command: make up MODE=kb)"

.env:
	@cp .env.example .env && echo "created .env from .env.example"

mode:  ## show what the current MODE runs
	@echo "MODE=$(MODE)"
	@echo "  services   $$($(DC) config --services | sort | tr '\n' ' ')"
	@echo "  agent      $(if $(HAS_MCP),yes,no)   KB_ENABLED=$(KB_ENABLED)  LANGFUSE_BASE_URL=$(or $(LANGFUSE_BASE_URL),-)"

up: .env  ## build and start the platform for the current MODE
	@# Switching down a mode must stop what the new one does not include;
	@# `up` alone never stops a service whose profile has been turned off.
	@extra=$$(comm -23 <($(DC) --profile '*' config --services | sort) \
	                   <($(DC) config --services | sort)); \
	  [ -z "$$extra" ] || $(DC) --profile '*' rm -sf $$extra >/dev/null 2>&1 || true
	$(DC) up -d --build --wait --wait-timeout 600
	@$(DC) run --rm gateway-keys
	@$(if $(HAS_KB),$(DC) run --rm kb-seed,true)
	@echo
	@echo "  MODE=$(MODE)"
	@echo "  Gateway         http://localhost:4000/v1   (try: make ask Q=\"hello\")"
	@$(if $(HAS_MCP),echo "  MCP server      http://mcp:8000/mcp   (agent network only)",true)
	@$(if $(HAS_KB),echo "  Knowledge base  postgresql://localhost:5433/kb   (see .env)",true)
	@$(if $(LANGFUSE_BASE_URL),echo "  Langfuse        http://localhost:3000   (see LANGFUSE_INIT_USER_* in .env)",true)
	@echo
	@if [ "$(MODE)" = gateway ]; then echo "  next:  make ask Q=\"...\"   or curl, see README"; \
	 else echo "  next:  make run  &&  make eval"; fi

need-agent:
	@if [ "$(MODE)" = gateway ]; then \
	  echo "MODE=gateway runs no agent. Use MODE=agent or higher: make up MODE=agent"; exit 1; fi

# Build the agent image on its own, then `run` without --build. `run --build`
# also rebuilds the agent's dependencies and recreates mock-llm and mcp with
# fresh IPs underneath a gateway that has cached the old ones - and when the
# two swap addresses, the gateway's model calls land on the MCP server.
AGENT_BUILD = $(DC) build -q agent

need-kb:
	@if [ -z "$(HAS_KB)" ]; then \
	  echo "MODE=$(MODE) runs no knowledge base. Use MODE=kb or full: make up MODE=kb"; exit 1; fi

run: .env need-agent  ## run the pipeline over input/items/ (ITEM=<id> for just one)
	@$(AGENT_BUILD)
	$(DC) run --rm agent $(ITEM)

eval: .env need-agent  ## score output/items/ against eval/expectations.json
	@$(AGENT_BUILD)
	$(DC) run --rm eval $(ITEM)

shell: .env need-agent  ## open a shell inside the agent container
	@$(AGENT_BUILD)
	$(DC) run --rm --entrypoint bash agent

ask: .env  ## one prompt to the gateway from your shell: Q="..." [ASK_MODEL=mock-model]
	@curl -sS http://localhost:4000/v1/chat/completions \
	  -H "Authorization: Bearer $$(sed -n 's/^CLI_GATEWAY_KEY=//p' .env)" \
	  -H "Content-Type: application/json" \
	  -d "$$(python3 -c 'import json,sys; print(json.dumps({"model": sys.argv[1], "messages": [{"role": "user", "content": sys.argv[2]}]}))' \
	        '$(or $(ASK_MODEL),mock-model)' '$(or $(Q),Say hello)')"; echo

keys: .env  ## apply gateway/agents.yaml: per-agent models, budgets, limits
	$(DC) run --rm gateway-keys

seed: .env need-kb  ## recreate the memory schema (honours KB_MODE)
	$(DC) run --rm kb-seed

kb: .env need-kb  ## open psql against the knowledge base
	$(DC) exec kb psql -U "$$(sed -n 's/^KB_USER=//p' .env)" -d "$$(sed -n 's/^KB_DB=//p' .env)"

logs:  ## follow logs from every service
	$(DC) logs -f --tail=50

ps:  ## show what is running
	$(DC) --profile '*' ps

down:  ## stop everything (every mode), keep the data
	$(DC) --profile '*' down --remove-orphans

clean:  ## stop everything and delete all volumes (incl. gateway spend), outputs and reports
	$(DC) --profile '*' down --remove-orphans -v
	@find output report -mindepth 1 ! -name .gitkeep -delete 2>/dev/null || true
	@echo "wiped: volumes (memory, gateway keys and spend, traces), output/, report/"

check: .env  ## verify the stack is wired up correctly for the current MODE
	@bash scripts/check.sh

tour: .env  ## guided tour of every mode, step by step (ARGS="--auto", "--from kb", "--fresh")
	@bash scripts/tour.sh $(ARGS)
