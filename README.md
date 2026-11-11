# Procurement-agent

Browser-assisted procurement agent 

PurchasePilot converts an employee's equipment request into a traceable supplier
comparison and an approval-ready purchase requisition. It researches supplier
websites with Stagehand, extracts product evidence, applies explicit purchasing
rules in Python, and explains its recommendation. A human reviews the exact
proposal before the system submits a requisition to a mock purchasing portal.

The prototype stops at requisition submission. It does not place supplier
orders, process payments, sign contracts, or guarantee supplier fulfillment.

## Status

This repository currently contains the **deterministic foundation** plus typed
stubs for the browser, LLM, and portal layers. Stagehand and live LLM calls are
intentionally not wired yet (Phase 1 feasibility spike).

## Setup

```bash
uv sync          # creates .venv with Python 3.12 and installs dependencies
uv run pytest    # run the deterministic test suite
```

## Layout

```text
app.py                     # Streamlit shell (thin UI delegating to controller)
procurement/               # deterministic core + typed stubs
  controller.py            # workflow controller / state machine
  states.py                # WorkflowState enum + transition graph
  schemas.py               # Pydantic models
  money.py                 # integer minor-unit (HKD cents) arithmetic
  rules.py                 # business rules (§5)
  approvals.py             # proposal hashing + approval binding
  seed.py                  # seeded synthetic data generator (seed 4110)
  repository.py            # SQLAlchemy models + SQLite session
  tools/                   # typed tool-contract stubs
  llm/                     # configurable LLM adapter stub + prompts
mock_sites/                # FastAPI supplier sites + mock portal
data/                      # policy + seed configuration
scripts/                   # seed_demo.py, run_evaluation.py
tests/                     # unit + integration + fixtures
```

## Key design constraints

- State, approval, and persistence live in `controller.py` / SQLite — **not** in
  the Streamlit UI layer — so Streamlit reruns cannot restart research or
  repeat writes.
- The LLM parses ambiguity and explains decisions; Python owns all arithmetic,
  eligibility, and state transitions.
- Supplier facts are acquired only through browser-visible evidence, never from
  seed data or hidden fixtures.
- All money is integer HKD minor units (cents); no floating-point arithmetic.

## Phase 1 feasibility spike (not yet done)

Install a pinned Stagehand/Python configuration, confirm local Chromium works,
and verify a real `observe` / `act` / `extract` round-trip against a mock site
before building the full interface. See the project proposal, §15.
