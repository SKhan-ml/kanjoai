# KanjoAI — Autonomous Invoice/Vendor Reconciliation Agent

Built for **AI HACK 2026** (theme: 業務を自律化するAIエージェント — an AI agent that
autonomizes a business operation), using **[OrcaRouter](https://www.orcarouter.ai)**
as the LLM gateway.

## The problem

Every company running an ERP/finance backend spends real human hours matching
incoming vendor invoices against purchase orders: extracting the numbers off
a scanned invoice, checking they match what was ordered, catching duplicates
and fraud, and deciding what needs a human's attention. KanjoAI does this
autonomously.

## Why it's an *agent*, not a single LLM call

Most "AI does X" hackathon demos are one prompt → one answer. KanjoAI runs
a multi-step loop, and every step writes to an audit trail so its decisions
stay explainable:

1. **Extract** — a vision-capable model reads the invoice image and returns
   structured JSON (vendor, invoice number, PO number, amount, currency, date,
   its own confidence).
2. **Match** — look up the purchase order the invoice claims to be against.
3. **Validate** — rule checks: does the PO exist, is the amount within
   tolerance, does the currency match, is this invoice number a duplicate.
4. **Decide** — combine rule outcomes + extraction confidence into
   `auto_approved` / `flagged_for_review` / `rejected`.
5. **Act** — persist the decision and the full reasoning trail.

If the cheap model is unsure (low confidence) or fails to return valid JSON,
the agent **automatically escalates to a stronger model** — it doesn't just
retry blindly, and it doesn't pay for the expensive model on every request.
This is OrcaRouter's cost/failover value proposition used as the actual
architecture of the agent, not just an API call bolted on.

## How this maps to the judging criteria

| Criterion | How it's addressed |
|---|---|
| **Security** | Every endpoint requires an `X-API-Key`; a **vendor-name match check** catches invoices that reference a valid PO number but were issued by a *different* vendor (misdirected/forged invoice) — rejected, not just flagged; unhandled errors return a generic message + error id, never a raw traceback. |
| **Cost-performance** | Tiered routing: cheap model first, strong model only on low confidence / failure (see `app/orcarouter_client.py`). Transient network errors are retried with backoff *before* paying for an escalation. |
| **Reliability/robustness** | Pydantic-validated LLM output and input (amounts must be positive, currency must be a real 3-letter code); decision severity is monotonic — a REJECTED invoice can never be silently downgraded by a later, milder check (see the regression test for this); **15-case pytest suite** covering every decision path, runnable offline with no OrcaRouter key; a global exception handler prevents any single bad request from crashing the process. |
| **Autonomy** | Multi-step extract→validate→decide→act loop with automatic model escalation on low confidence, and automatic retry-with-backoff on transient failures — not a single classification call. |
| **Idea/originality** | Grounded in a real ERP/backend problem (invoice reconciliation), not a generic chatbot demo. |

## Running the tests

```bash
pip install -r requirements.txt   # includes pytest
pytest tests/ -v
```

15 tests, all offline (no OrcaRouter key needed) — they exercise the
decision engine directly: auto-approve, amount-tolerance flagging,
vendor-mismatch rejection, duplicate-invoice rejection, missing-PO
flagging, low-confidence flagging, and a regression test proving a
REJECTED status is never downgraded by a later check.

## Architecture

```
Invoice image ──▶ OrcaRouter (vision, cheap model)
                        │  low confidence / invalid JSON?
                        ▼
                  OrcaRouter (vision, strong model)  ── escalation
                        │
                  ExtractedInvoice (validated)
                        │
              match PO ─┼─ check duplicate ─┼─ check tolerance
                        ▼
              AUTO_APPROVED / FLAGGED / REJECTED
                        │
                  AuditLog (every step, every model, every reason)
```

## Stack

FastAPI · SQLAlchemy (SQLite by default, swap `DATABASE_URL` for Postgres) ·
Pydantic · OpenAI SDK pointed at OrcaRouter's OpenAI-compatible endpoint
(`https://api.orcarouter.ai/v1`).

## Try it — live demo UI

`uvicorn app.main:app` also serves a small self-contained demo page at
`http://localhost:8000/` — no separate frontend build, no extra process.
It ships with four sample invoices (clean match, amount over tolerance,
vendor mismatch, duplicate invoice number) so anyone can click through
all four decision paths in under a minute, or drop in their own invoice
image, or register a brand-new purchase order and test a scenario of
their own. Every run also shows up in the recent-invoices list with its
full audit trail.

## Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in ORCAROUTER_API_KEY, set DRY_RUN=false
python -m scripts.seed_demo_data
uvicorn app.main:app --reload
```

Try it without any API key at all — `DRY_RUN=true` (the default) runs the
whole pipeline against a deterministic stub response, so the demo works
even before an OrcaRouter key is wired in:

```bash
curl -X POST http://localhost:8000/invoices/process \
  -H "X-API-Key: demo-local-api-key" \
  -F "file=@sample_invoice.png;type=image/png"
```

Then inspect the decision + full reasoning trail:

```bash
curl http://localhost:8000/invoices/1 -H "X-API-Key: demo-local-api-key"
```

## Endpoints

- `POST /vendors`, `GET /vendors`
- `POST /purchase-orders`, `GET /purchase-orders`
- `POST /invoices/process` — upload an invoice image, get back the agent's decision
- `GET /invoices`, `GET /invoices/{id}` — includes the full audit trail

## Submitted for AI HACK 2026

Built using **OrcaRouter** for LLM routing. See the accompanying Qiita/Zenn
write-up for the full story of what was built during the hackathon.
