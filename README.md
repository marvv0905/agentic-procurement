# Agentic Procurement

Browser-assisted procurement agent

Agentic Procurement converts an employee's equipment request into a traceable supplier
comparison and an approval-ready purchase requisition. It researches supplier
websites with Stagehand, extracts product evidence, applies explicit purchasing
rules in Python, and explains its recommendation. A human (a manager) reviews
and approves before the order is placed.

The prototype stops at order placement. It does not process payments, sign
contracts, or guarantee supplier fulfillment.

## What it can do

**Ordering (any employee)**
- **Chatbot** — persistent LLM chat sessions that turn a plain-language request
  into a purchase order. Messages and the running draft survive refreshes
  (the 5 most recent sessions are kept); confirming a draft creates the order
  and starts agent research.
- **Order Request wizard** — type what you need in your own words; the same
  intake parser reads the sentence and fills the form (category, specs,
  quantity, budget). Specs you set become hard checks; anything left as
  *No preference* is never enforced. Half-finished requests autosave as DRAFT
  rows that survive a refresh and can be resumed or discarded.
- **Typed catalog + free text** — three typed categories (monitor, keyboard,
  office chair) are validated against explicit attributes. Anything else
  routes to the free-text **Other** flow: the agent searches supplier sites
  for the request and rejects listings that don't mention every must-have
  keyword — so users can order e.g. noise-cancelling headphones.
- **All Orders** — read-only history of every order for everyone, with the
  full decision trail.

**Agent research (background thread per order)**
- Browses **every approved supplier site** with a real Stagehand browser,
  extracting offers from browser-visible evidence — never from seed data or
  hidden fixtures. Works on the seeded mock sites and on **live aggregator
  sites** (Price.com.hk is seeded as an approved supplier).
- A **deterministic Python rules engine** — not the LLM — performs all
  arithmetic, eligibility, and state transitions, and selects the lowest
  verified compliant total. Unknown facts stay unknown (three-valued
  logic), never silently zero.
- **Bilingual semantic matching** — a local, free multilingual sentence
  embedding model matches paraphrases ("ANC" vs "noise cancelling") and
  cross-language gaps (降噪 vs "active noise cancelling").
- A **live run console** per order: what the agent understood, what it is
  doing (live browser viewer + step track), and per-offer PASS / UNKNOWN /
  FAIL verdicts with reasons — rebuilt deterministically from persisted
  products, so the panel is correct even after a restart.

**Management (managers only)**
- **Pending Approvals** — the dedicated decision view; rejecting requires a
  reason, and every decision lands in the human decision trail.
- **Contact Sellers** — for approved (PLACED) orders, ask the seller-contact
  routine to draft a WhatsApp message. The routine asks the manager for
  approval before anything is sent, and only ever receives the order number,
  never the order details. Optional; configure via webhook env vars.
- **Suppliers** — add a plain catalog site (just a URL) or an aggregator /
  search site (also a `{query}` search URL) with zero agent code changes.
- **User Management** — add employees; pbkdf2 password hashing; employee and
  manager roles.

**Engineering**
- Deterministic pytest suite (unit + integration; no LLM or browser needed).
- Evaluation harness entry point (`scripts/run_evaluation.py`) and per-offer
  verdict rebuilding (`procurement/evaluation.py`).
- All money in integer HKD minor units (cents); no floating-point arithmetic.

## Setup

```bash
uv sync          # creates .venv with Python 3.12 and installs dependencies
uv run pytest    # run the deterministic test suite
```

Copy `example.env` to `.env` and fill in the values:

| Variable | Required | Purpose |
|---|---|---|
| `DEEPSEEK_API_KEY` | yes | Text LLM: intake parser (chatbot + wizard) and decision explanations |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` | no | Defaults to `https://api.deepseek.com` / `deepseek-chat` |
| `OPENROUTER_API_KEY` | recommended | Vision model for the Stagehand browser agent |
| `OPENROUTER_BASE_URL` / `OPENROUTER_MODEL` | no | Use a vision-capable model, e.g. `qwen/qwen3-vl-235b-a22b-instruct` |
| `DATABASE_URL` | no | SQLite location (default `sqlite:///data/Agentic Procurement.db`) |
| `MOCK_BASE_URL` | no | Where the mock supplier sites run (default `http://127.0.0.1:8100`) |
| `STAGEHAND_HEADLESS` | no | `true`/`false` force headless/visible research; unset = headless for mock sites, visible for aggregators |
| `DEMO_ADMIN_PASSWORD` | no | Override the seeded admin password (`admin123`) |
| `GROK_WEBHOOK_URL` / `GROK_WEBHOOK_AUTHORIZATION` | no | Enable the Contact Sellers page (seller-contact routine webhook) |

Without OpenRouter credentials the browser agent falls back to DeepSeek
(text-only). Without the webhook variables the Contact Sellers page simply
reports that seller contact isn't configured.

### Semantic matching (local sentence embeddings)

Keyword checks and listing ranking use a **local, free multilingual sentence
embedding model** so paraphrases ("ANC" vs "noise cancelling") and
cross-language gaps (降噪 vs "active noise cancelling") match without
depending on the extraction LLM's translation. Warm the local cache once:

```bash
uv run python scripts/download_embedding_model.py
```

Without the cached model everything degrades gracefully to exact substring
matching — a purchase run never fails because the model is missing.

## Running the demo

```bash
# 1. The mock supplier sites (one app serves all three suppliers)
uv run uvicorn mock_sites.server:app --port 8100

# 2. The Streamlit UI (creates/seed data/Agentic Procurement.db on first run)
uv run streamlit run app.py

# 3. Sign in with the seeded manager account
#    admin / admin123   (override with DEMO_ADMIN_PASSWORD)
```

Aggregator suppliers (Price.com.hk) sit behind Cloudflare, so their research
automatically runs in a **visible browser window** — you can watch it shop.
Set `STAGEHAND_HEADLESS=false` to make all research visible, `true` to force
headless.

Managers add employees under **User Management**. Any user can create an order
via the **Chatbot** or the **Order Request** wizard — typed categories
(monitor / keyboard / chair) or the **Other** free-text option; only managers
approve on **Pending Approvals**. Research runs in a background thread
writing order status to SQLite, so Streamlit reruns never restart it.

### Deep-scrape fence

For aggregator suppliers the agent searches the request phrase, then opens at
most **6 product pages**, taking at most **5 shop offers** each, within **120s
and 25 browser actions** per supplier — then the deterministic rules engine
picks the best compliant offer inside that fence. Availability is classified
by the extractor (in stock / low stock / preorder / out of stock); shipping
that the site does not list is excluded from the verified total and shown as
unverified on the approval card.

### Adding a live supplier

Managers add suppliers on the **Suppliers** page. For a plain catalog site,
just the site URL is needed. For an aggregator/search site (like Price.com.hk)
also set a **Catalog / search URL** containing a `{query}` placeholder, e.g.
`https://www.price.com.hk/search.php?q={query}` — zero agent code changes.

## Layout

```text
app.py                     # Streamlit shell: login gate + st.navigation router
procurement/               # deterministic core + agent blackbox
  controller.py           # workflow controller / state machine (in-memory)
  states.py               # WorkflowState enum + transition graph
  schemas.py              # Pydantic models
  catalog.py              # category/attribute catalog (single source of truth)
  money.py                 # integer minor-unit (HKD cents) arithmetic
  rules.py                 # business rules (§5)
  auth.py                  # pbkdf2 password hashing + login
  order_service.py         # order lifecycle + human decisions
  chat_service.py          # chat session/message persistence (5-session cap)
  evaluation.py            # per-offer verdicts rebuilt from the human record
  embeddings.py            # local multilingual sentence embeddings
  research.py              # blackbox research orchestrator (background thread)
  seed.py                  # synthetic data generator + DB seeding
  repository.py            # SQLAlchemy models + SQLite session
  tools/                   # browser (Stagehand) + supplier research + portal stubs
  llm/                     # configurable LLM adapter + prompts
  ui/                      # Streamlit page renderers + live run console
mock_sites/                # FastAPI supplier sites + mock portal
data/                      # policy + seed configuration
scripts/                   # seed_demo.py, inspect_db.py, run_evaluation.py
tests/                     # unit + integration + fixtures
```

## Key design constraints

- The database is the **human record** (users, suppliers, products, orders,
  human decisions, chat persistence). Agent internals (runs, evidence, audit
  events) are deliberately not persisted — the agent is a blackbox whose
  outputs touch SQLite only.
- Order lifecycle transitions are enforced in `order_service.py` — never in the
  UI layer — so Streamlit reruns cannot repeat writes or restart research.
- The LLM parses ambiguity and explains decisions; Python owns all arithmetic,
  eligibility, and state transitions.
- Supplier facts are acquired only through browser-visible evidence, never from
  seed data or hidden fixtures. Unknown facts stay unknown (three-valued
  logic), never silently zero.
- All money is integer HKD minor units (cents); no floating-point arithmetic.

## Phase 1 feasibility spike

See `scripts/browser_smoke_test.py` for the Stagehand + local Chrome + DeepSeek
round-trip check (`uv run python scripts/browser_smoke_test.py --target local`
with the mock sites running).
