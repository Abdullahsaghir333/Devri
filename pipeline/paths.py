"""Artifact paths relative to project root."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TICKETS_PATH = ROOT / "tickets.json"
CONFIG_PATH = ROOT / "triage_config.json"
NORMALIZED_PATH = ROOT / "normalized_tickets.json"
PREDICTIONS_PATH = ROOT / "triage_predictions.json"
OVERRIDES_PATH = ROOT / "review_overrides.json"
FINAL_QUEUE_PATH = ROOT / "final_queue.json"
SUMMARY_PATH = ROOT / "queue_summary.md"
ESCALATIONS_PATH = ROOT / "escalations.json"
LLM_LOG_PATH = ROOT / "llm_calls.jsonl"
STATE_PATH = ROOT / "pipeline_state.json"
RECOVERY_LOG_PATH = ROOT / "triage_recovery.log"
