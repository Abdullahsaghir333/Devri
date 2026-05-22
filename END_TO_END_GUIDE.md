# Support Ticket Triage Pipeline — End-to-End Guide

This document explains the **complete flow** of the project, **what technologies are used**, and **how to verify** everything works correctly.

---

## 1. What This Project Does

You have a **replayable pipeline** that:

1. Reads customer support tickets from `tickets.json`
2. Reads business rules from `triage_config.json`
3. **Normalizes** ticket text in pure Python (no AI)
4. Sends all tickets to **Google Gemini** in **one batch call** for classification
5. Pauses for **human review** so you can fix category/priority
6. Builds a **final agent queue** with routing and a **markdown summary**
7. Flags uncertain tickets for **escalation**
8. Can be **validated** automatically with `validate.py`

The evaluator (or you) can swap `tickets.json` / `triage_config.json` for different fixtures — the code does not hardcode sample answers.

---

## 2. Technology Stack

| Layer | What we used | Purpose |
|-------|----------------|---------|
| Language | **Python 3** | Main implementation |
| Environment | **`venv`** | Isolated dependencies per project |
| LLM (primary) | **Google Gemini API** | Batch ticket classification + reply drafts |
| LLM (fallback) | **Groq API** | Used if Gemini fails or is unavailable |
| SDK | **`google-genai`**, **`groq`** | Gemini + Groq clients |
| Config | **`.env` file** | API key and model settings (gitignored) |
| Env loader | **`pipeline/env.py`** | Loads `.env` with **override** (project beats global OS vars) |
| Data format | **JSON** | All inputs/outputs |
| Audit log | **`llm_calls.jsonl`** | One JSON line per LLM call |
| Validation | **`validate.py`** | Automated checks on artifacts |
| Automation | **`Makefile`** | `make run`, `make validate`, `make clean` |

### Environment variables (`.env`)

```env
GEMINI_API_KEY=your_gemini_key_here
GROQ_API_KEY=your_groq_key_here
TRIAGE_LLM_PROVIDER=gemini
TRIAGE_LLM_MODEL=gemini-2.0-flash
TRIAGE_GROQ_MODEL=llama-3.3-70b-versatile
```

- **`load_dotenv(override=True)`** runs at the start of `run_pipeline.py`
- Values in `.env` **replace** any `GEMINI_API_KEY` / `GOOGLE_API_KEY` set globally on your PC
- If `.env` has `GEMINI_API_KEY` only, global `GOOGLE_API_KEY` is removed so the SDK does not use the wrong key

### Fallback (no API / offline)

If there is no key or `google-genai` is missing, `pipeline/triage.py` uses a **keyword heuristic** on `text_for_model`. The pipeline still completes and writes all artifacts.

---

## 3. Repository Layout

```
MERYDEV/
├── tickets.json              # INPUT: raw tickets
├── triage_config.json        # INPUT: categories, priorities, routing, reply rules
├── .env                      # SECRET: your Gemini key (not committed)
├── .env.example              # Template for .env
├── run_pipeline.py           # MAIN: run full pipeline
├── validate.py               # Verify all outputs
├── requirements.txt          # google-genai
├── Makefile                  # Shortcuts
├── README.md                 # Quick reference
├── planning.txt              # Build plan + design notes
├── END_TO_END_GUIDE.md       # This file
│
├── pipeline/                 # Core package
│   ├── paths.py              # File paths for all artifacts
│   ├── stages.py             # Enforced stage order
│   ├── env.py                # .env loader (override)
│   ├── config.py             # Config load + validation helpers
│   ├── normalize.py          # Deterministic preprocessing
│   ├── triage.py             # Gemini batch + validation + fallback
│   ├── review.py             # Human override checkpoint
│   ├── queue.py              # Final queue, summary, escalations
│   └── llm_log.py            # llm_calls.jsonl writer
│
└── (generated after run)
    ├── normalized_tickets.json
    ├── triage_predictions.json
    ├── review_overrides.json
    ├── final_queue.json
    ├── queue_summary.md
    ├── escalations.json
    ├── llm_calls.jsonl
    ├── pipeline_state.json
    └── triage_recovery.log    # Only if a ticket needed recovery
```

---

## 4. Pipeline Stages (Enforced Order)

The pipeline **cannot skip steps**. `pipeline/stages.py` only allows moving to the **next** stage in this list:

```
INIT
  → INPUTS_LOADED
  → TICKETS_NORMALIZED      ← normalization BEFORE any LLM
  → TRIAGE_PREDICTED        ← single Gemini call for all tickets
  → HUMAN_REVIEW_COMPLETE   ← interactive overrides
  → FINAL_QUEUE_GENERATED   ← final outputs only after review
  → VALIDATION_COMPLETE     (when you run validate.py)
  → RESULTS_FINALISED
```

`pipeline_state.json` stores `current_stage` and `history` for auditing.

---

## 5. End-to-End Flow (Step by Step)

### Step 0 — Setup (one time)

```powershell
cd c:\Users\abdul\MYDOCUMENTS\MERYDEV
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Edit .env and set GEMINI_API_KEY=...
```

---

### Step 1 — Start pipeline (`run_pipeline.py`)

**Command:**

```powershell
venv\Scripts\activate
python run_pipeline.py
```

**What happens internally:**

1. **`load_dotenv(override=True)`**  
   Loads `.env` and overrides global environment variables.

2. **Reset artifacts (default)**  
   Deletes previous `normalized_tickets.json`, `triage_predictions.json`, etc., so each run is fresh.

3. **Stage: INIT → INPUTS_LOADED**  
   - Reads `tickets.json` (array of tickets)  
   - Reads `triage_config.json` (allowed categories, priorities, routing, reply style)

---

### Step 2 — Deterministic normalization (NO LLM)

**Module:** `pipeline/normalize.py`  
**Stage:** `TICKETS_NORMALIZED`  
**Output:** `normalized_tickets.json`

For each ticket from `tickets.json`:

| Field | How it is built |
|-------|------------------|
| `ticket_id`, `subject`, `message`, `channel`, `created_at` | Copied from input |
| `text_for_model` | `"Subject: {subject}\n\nMessage: {message}"` with whitespace collapsed |
| `char_count` | `len(text_for_model)` |

**Important:** The LLM is never asked to clean or merge raw text. This step is 100% Python.

**Example shape:**

```json
{
  "ticket_id": "T-1001",
  "subject": "Charged twice for my deposit",
  "message": "Hi, I made one deposit...",
  "channel": "email",
  "created_at": "2026-05-10T09:15:00Z",
  "text_for_model": "Subject: Charged twice...\n\nMessage: Hi, I made...",
  "char_count": 142
}
```

---

### Step 3 — Gemini batch triage (ONE LLM call)

**Module:** `pipeline/triage.py`  
**Stage:** `TRIAGE_PREDICTED`  
**Outputs:** `triage_predictions.json`, `llm_calls.jsonl`

**Flow:**

1. Build one prompt containing **all** tickets (`ticket_id` + `text_for_model` only).
2. Try **Gemini** first (`gemini-2.0-flash` by default).
3. If Gemini fails (API error, bad JSON, or missing tickets), try **Groq** (`llama-3.3-70b-versatile` by default).
4. Parse JSON array from whichever provider succeeded.
4. For **each** ticket, validate and finalize:
   - `category` ∈ `allowed_categories` from config
   - `priority` ∈ `allowed_priorities` from config
   - `route_to` = `routing_rules[category]` (**computed in code**, not from model)
   - `suggested_reply` truncated to `reply_style.max_words`
   - `confidence` (0.0–1.0) for escalation rules
5. If one ticket is missing or malformed → **per-ticket fallback** (keyword heuristic), logged in `triage_recovery.log` — pipeline does not crash.

**Prediction record shape:**

```json
{
  "ticket_id": "T-1001",
  "category": "billing_issue",
  "priority": "high",
  "reason": "Customer reports duplicate charge on deposit",
  "suggested_reply": "Thank you for reaching out...",
  "route_to": "payments_queue",
  "confidence": 0.92
}
```

**`llm_calls.jsonl` line example:**

```json
{
  "stage": "TRIAGE_PREDICTED",
  "timestamp": "2026-05-23T12:00:00Z",
  "provider": "gemini",
  "model": "gemini-2.0-flash",
  "prompt_hash": "a1b2c3...",
  "input_artifacts": ["normalized_tickets.json", "triage_config.json"],
  "output_artifact": "triage_predictions.json"
}
```

---

### Step 4 — Human review checkpoint (interactive)

**Module:** `pipeline/review.py`  
**Stage:** `HUMAN_REVIEW_COMPLETE`  
**Output:** `review_overrides.json`

**Terminal output** shows each ticket:

```text
--- Triage predictions (review) ---
  T-1001: category=billing_issue, priority=high, route_to=payments_queue
  ...
```

**Prompt:**

```text
Enter any overrides as: ticket_id,category,priority
Press Enter on an empty line when done.
```

**Example override:**

```text
T-1002,account_access,urgent
```

- Override values must be valid per `triage_config.json`.
- Press **Enter on empty line** when finished.
- Saves `review_overrides.json` (empty array `[]` if no changes).

**Override record shape:**

```json
{
  "ticket_id": "T-1002",
  "old_category": "account_access",
  "new_category": "account_access",
  "old_priority": "high",
  "new_priority": "urgent"
}
```

**Applied in memory:** `apply_overrides()` updates category/priority and **recomputes `route_to`** from routing rules.

**Non-interactive mode** (no typing, for quick tests):

```powershell
python run_pipeline.py --non-interactive
```

---

### Step 5 — Final queue + summary + escalations

**Module:** `pipeline/queue.py`  
**Stage:** `FINAL_QUEUE_GENERATED`  
**Outputs:** `final_queue.json`, `queue_summary.md`, `escalations.json`

Uses **post-review** values (overrides actually change output).

**`final_queue.json` per ticket:**

```json
{
  "ticket_id": "T-1001",
  "final_category": "billing_issue",
  "final_priority": "high",
  "final_route_to": "payments_queue",
  "suggested_reply": "...",
  "was_overridden": false
}
```

**`queue_summary.md` includes:**

- Total ticket count
- Count by final category
- Count by final priority
- Breakdown by destination queue
- List of overridden tickets

**`escalations.json`** (deterministic rules):

- Escalate if `category == "other"`
- Escalate if `confidence < 0.60`

---

## 6. Visual Flow Diagram

```text
  tickets.json ──────┐
                     ├──► Load inputs (INPUTS_LOADED)
  triage_config.json ┘
                     │
                     ▼
              Normalize (Python only)
                     │
                     ▼
         normalized_tickets.json
                     │
                     ▼
              Gemini (1 batch call)
                     │
                     ▼
         triage_predictions.json + llm_calls.jsonl
                     │
                     ▼
              Human review (terminal)
                     │
                     ▼
           review_overrides.json
                     │
                     ▼
         Apply overrides + build queue
                     │
         ┌───────────┼───────────┐
         ▼           ▼           ▼
  final_queue.json  queue_summary.md  escalations.json
```

---

## 7. How to Run (Commands Cheat Sheet)

| Goal | Command |
|------|---------|
| Full pipeline (with review) | `python run_pipeline.py` |
| Skip review prompts | `python run_pipeline.py --non-interactive` |
| Keep old artifacts | `python run_pipeline.py --no-reset` |
| Validate outputs | `python validate.py` |
| Makefile run | `make run` |
| Makefile validate | `make validate` |
| Delete generated files | `make clean` |

---

## 8. How to Verify (Complete Checklist)

### 8.1 Automated validation (recommended)

```powershell
venv\Scripts\activate
python run_pipeline.py --non-interactive
python validate.py
```

**Expected:**

```text
VALIDATION PASSED
  Artifacts OK under C:\Users\abdul\MYDOCUMENTS\MERYDEV
```

If it fails, `validate.py` prints specific errors (missing file, bad category, override not applied, etc.).

---

### 8.2 Manual verification checklist

#### A. Files exist after run

- [ ] `normalized_tickets.json`
- [ ] `triage_predictions.json`
- [ ] `review_overrides.json`
- [ ] `final_queue.json`
- [ ] `queue_summary.md`
- [ ] `escalations.json`
- [ ] `llm_calls.jsonl`
- [ ] `pipeline_state.json`

#### B. Normalization happened before LLM

- [ ] `normalized_tickets.json` exists and has one entry per ticket in `tickets.json`
- [ ] Each `text_for_model` starts with `Subject:` and contains `\n\nMessage:`
- [ ] `char_count` equals the length of `text_for_model`
- [ ] `pipeline_state.json` history shows `TICKETS_NORMALIZED` before `TRIAGE_PREDICTED`
- [ ] `normalized_tickets.json` file timestamp is **older than** `triage_predictions.json`

#### C. Predictions are valid

- [ ] One prediction per `ticket_id` in `tickets.json`
- [ ] Every `category` is in `triage_config.json` → `allowed_categories`
- [ ] Every `priority` is in `allowed_priorities`
- [ ] Every `route_to` matches `routing_rules[category]` exactly
- [ ] `suggested_reply` word count ≤ `reply_style.max_words` (80 in sample config)

#### D. Human review affects output

1. Run interactively: `python run_pipeline.py`
2. Enter one override, e.g. `T-1003,bug_report,urgent`
3. Check:
   - [ ] `review_overrides.json` contains that override with correct `old_*` / `new_*`
   - [ ] `final_queue.json` shows `final_category` / `final_priority` **updated** for T-1003
   - [ ] `final_route_to` = `technical_queue` (routing for `bug_report`)
   - [ ] `was_overridden: true` for T-1003
   - [ ] `queue_summary.md` lists T-1003 under overridden tickets

#### E. Gemini was used (not fallback)

Open `llm_calls.jsonl`:

- [ ] `"provider": "gemini"`
- [ ] `"model": "gemini-2.0-flash"` (or your `TRIAGE_LLM_MODEL`)
- [ ] `input_artifacts` includes `normalized_tickets.json` and `triage_config.json`

If you see `"provider": "deterministic_fallback"`, check `.env` key and `pip install google-genai`.

#### F. Escalations

- [ ] Any ticket with `category: "other"` appears in `escalations.json`
- [ ] Any ticket with `confidence < 0.60` appears in `escalations.json`

#### G. Reproducibility from clean run

```powershell
make clean
python run_pipeline.py --non-interactive
python validate.py
```

- [ ] All artifacts regenerated
- [ ] Validation passes again

---

### 8.3 Quick inspect commands (PowerShell)

```powershell
# Count tickets vs predictions
(Get-Content tickets.json | ConvertFrom-Json).Count
(Get-Content triage_predictions.json | ConvertFrom-Json).Count

# View pipeline stage history
Get-Content pipeline_state.json

# View last LLM call
Get-Content llm_calls.jsonl -Tail 1

# View summary
Get-Content queue_summary.md
```

---

## 9. What `validate.py` Checks (Summary)

| Check | Why it matters |
|-------|----------------|
| Required files exist | Pipeline completed |
| Valid JSON | Files are parseable |
| `text_for_model` matches deterministic builder | No LLM normalization |
| Normalization before predictions (state + mtime) | Stage order enforced |
| One prediction per ticket | Complete batch coverage |
| Categories/priorities in config | Controlled label set |
| `route_to` matches routing rules | Routing not invented by model |
| Overrides valid and applied in `final_queue.json` | Human review has effect |
| Reply word limit | Reply style enforced |
| Summary contains total count | Report completeness |
| Escalation reasons | Escalation rules correct |

---

## 10. Troubleshooting

| Problem | Likely cause | Fix |
|---------|----------------|-----|
| Uses wrong API key | Global `GOOGLE_API_KEY` | Ensure `.env` has `GEMINI_API_KEY`; rerun (override clears global GOOGLE) |
| Gemini fails, need Groq | Gemini quota/error | Add `GROQ_API_KEY` to `.env`; pipeline tries Groq automatically |
| `deterministic_fallback` in log | Both APIs failed / no keys | Set keys in `.env`, run `pip install -r requirements.txt` |
| `ModuleNotFoundError: google` | venv not active / not installed | `venv\Scripts\activate` then `pip install -r requirements.txt` |
| Validation: override not applied | Typo in ticket_id or invalid category | Use exact id and config values |
| `triage_recovery.log` entries | Gemini returned incomplete JSON | Normal; per-ticket fallback ran — check log |
| Interactive run hangs | Waiting for input | Type overrides or press Enter on empty line |

---

## 11. Related Docs

| File | Contents |
|------|----------|
| `planning.txt` | Original build plan, architecture, design decisions |
| `README.md` | Short quick-start |
| `.env.example` | Environment template |
| `END_TO_END_GUIDE.md` | This complete flow + verification guide |

---

## 12. Minimum Verification Path (30 seconds)

```powershell
cd c:\Users\abdul\MYDOCUMENTS\MERYDEV
venv\Scripts\activate
python run_pipeline.py --non-interactive
python validate.py
```

If you see **VALIDATION PASSED** and `llm_calls.jsonl` shows `"provider": "gemini"`, your end-to-end pipeline is working correctly with your project `.env` API key.
