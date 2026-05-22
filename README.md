# Support Ticket Triage Pipeline

Replayable pipeline that normalizes tickets, classifies them with an LLM (or deterministic fallback), supports human review overrides, and produces a final agent queue plus summary.

## Quick start

```bash
pip install -r requirements.txt
python run_pipeline.py
python validate.py
```

Non-interactive run (no overrides, for CI):

```bash
python run_pipeline.py --non-interactive
```

## Inputs

- `tickets.json` — raw support tickets
- `triage_config.json` — allowed categories, priorities, reply style, routing rules

## Pipeline stages

`INIT` → `INPUTS_LOADED` → `TICKETS_NORMALIZED` → `TRIAGE_PREDICTED` → `HUMAN_REVIEW_COMPLETE` → `FINAL_QUEUE_GENERATED`

Stage order is enforced in code. The final queue is not built until triage and human review complete.

## Outputs

| File | Description |
|------|-------------|
| `normalized_tickets.json` | Deterministic preprocessing (before any LLM call) |
| `triage_predictions.json` | Batch triage results |
| `review_overrides.json` | Human corrections |
| `final_queue.json` | Post-review routing queue |
| `queue_summary.md` | Counts and override list |
| `escalations.json` | Low-confidence / `other` tickets |
| `llm_calls.jsonl` | LLM call audit log |
| `pipeline_state.json` | Stage history |

## Human review

After predictions print to the terminal:

```text
Enter any overrides as: ticket_id,category,priority
Press Enter on an empty line when done.
```

Example: `T-1002,account_access,urgent`

## Python virtual environment

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your Gemini API key.

## LLM configuration (Gemini)

Put settings in `.env` (recommended). When you run the pipeline, `.env` **overrides**
any `GEMINI_API_KEY` or `GOOGLE_API_KEY` already set in your system environment.

- `GEMINI_API_KEY` — Google Gemini API key (tried first)
- `GROQ_API_KEY` — Groq API key (used if Gemini fails or is unavailable)
- `TRIAGE_LLM_PROVIDER=gemini` — Gemini then Groq fallback (default)
- `TRIAGE_LLM_PROVIDER=groq` — Groq only
- `TRIAGE_LLM_MODEL=gemini-2.0-flash` — Gemini model
- `TRIAGE_GROQ_MODEL=llama-3.3-70b-versatile` — Groq model
- `TRIAGE_LLM_PROVIDER=fallback` — keyword heuristic only (no APIs)

If both APIs fail, per-ticket keyword heuristic is used and logged in `triage_recovery.log`.

## Validation

```bash
make validate
# or
python validate.py
```

Checks artifacts, config constraints, normalization order, routing, overrides, and reply word limits.
