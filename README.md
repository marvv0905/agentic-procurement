# PurchasePilot

Browser-assisted procurement agent — student prototype (ISOM4110 Topic 12).

PurchasePilot converts an employee's equipment request into a traceable supplier
comparison and an approval-ready purchase requisition. It researches supplier
websites with Stagehand, extracts product evidence, applies explicit purchasing
rules in Python, and explains its recommendation. A human reviews the exact
proposal before the system submits a requisition to a mock purchasing portal.

The prototype stops at requisition submission. It does not place supplier
orders, process payments, sign contracts, or guarantee supplier fulfillment.

## Status

The **deterministic foundation** (schemas, rules, state machine, approvals,
seed data) is implemented and tested. Stagehand v4 is wired to local Chrome
through a DeepSeek bring-your-own-LLM callback, and the synthetic supplier
sites render real seeded data. Live LLM calls need a `DEEPSEEK_API_KEY`.

## Setup

```bash
uv sync                       # creates .venv (Python 3.12) + installs deps
cp .env.example .env          # then add your DEEPSEEK_API_KEY
uv run pytest                 # run the deterministic test suite
```

## Browser smoke test (Phase 1 spike)

```bash
# terminal 1: start the synthetic supplier sites
uv run uvicorn mock_sites.server:app --port 8100

# terminal 2: extract a monitor from the synthetic catalogue
uv run python scripts/browser_smoke_test.py --target local

# (optional) sanity-check Chrome + DeepSeek against a public page first
uv run python scripts/browser_smoke_test.py --target public
```

A visible Chrome window opens so you can watch each step.

## Run the Streamlit UI

```bash
uv run streamlit run app.py
```

Opens at http://localhost:8501. The UI is a thin shell delegating to the
controller; the request flow is wired in a later phase.

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
  tools/                   # browser (Stagehand), suppliers, portal
  llm/                     # DeepSeek adapter + Stagehand generate callback
mock_sites/                # FastAPI supplier sites + mock portal (renders seed)
data/                      # policy + seed configuration
scripts/                   # seed_demo.py, run_evaluation.py, browser_smoke_test.py
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

## Phase 1 feasibility spike

Stagehand v4 + local Chrome + DeepSeek callback are wired, and the synthetic
sites render. Remaining before full interface work: confirm a real
`observe` / `act` / `extract` round-trip (the browser smoke test above) and
validate the exact `DEEPSEEK_MODEL` string against DeepSeek's API. See the
project proposal, §15.
