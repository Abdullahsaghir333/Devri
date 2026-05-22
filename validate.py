#!/usr/bin/env python3
"""Validate pipeline artifacts and constraints."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pipeline.config import load_config, route_for_category, word_count
from pipeline.normalize import build_text_for_model
from pipeline.paths import (
    CONFIG_PATH,
    ESCALATIONS_PATH,
    FINAL_QUEUE_PATH,
    LLM_LOG_PATH,
    NORMALIZED_PATH,
    OVERRIDES_PATH,
    PREDICTIONS_PATH,
    ROOT,
    STATE_PATH,
    SUMMARY_PATH,
    TICKETS_PATH,
)
from pipeline.stages import PipelineStage


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _error(msg: str, errors: list[str]) -> None:
    errors.append(msg)


def validate(errors: list[str]) -> None:
    required = [
        TICKETS_PATH,
        CONFIG_PATH,
        NORMALIZED_PATH,
        PREDICTIONS_PATH,
        OVERRIDES_PATH,
        FINAL_QUEUE_PATH,
        SUMMARY_PATH,
    ]
    for path in required:
        if not path.exists():
            _error(f"Missing required artifact: {path.name}", errors)
    if errors:
        return

    tickets = _load_json(TICKETS_PATH)
    config = _load_config_safe(CONFIG_PATH, errors)
    if config is None:
        return
    normalized = _load_json(NORMALIZED_PATH)
    predictions = _load_json(PREDICTIONS_PATH)
    overrides = _load_json(OVERRIDES_PATH)
    final_queue = _load_json(FINAL_QUEUE_PATH)
    summary_text = SUMMARY_PATH.read_text(encoding="utf-8")

    if not isinstance(tickets, list):
        _error("tickets.json must be a JSON array", errors)
    if not isinstance(normalized, list):
        _error("normalized_tickets.json must be a JSON array", errors)
    if not isinstance(predictions, list):
        _error("triage_predictions.json must be a JSON array", errors)
    if not isinstance(overrides, list):
        _error("review_overrides.json must be a JSON array", errors)
    if not isinstance(final_queue, list):
        _error("final_queue.json must be a JSON array", errors)

    ticket_ids = {t["ticket_id"] for t in tickets if isinstance(t, dict)}
    if len(ticket_ids) != len(tickets):
        _error("Duplicate or invalid ticket_id in tickets.json", errors)

    # Deterministic normalization checks
    norm_by_id = {}
    for raw, norm in zip(tickets, normalized):
        if not isinstance(norm, dict):
            continue
        tid = norm.get("ticket_id")
        norm_by_id[tid] = norm
        expected_text = build_text_for_model(
            str(raw.get("subject", "")), str(raw.get("message", ""))
        )
        if norm.get("text_for_model") != expected_text:
            _error(
                f"{tid}: text_for_model does not match deterministic builder",
                errors,
            )
        if norm.get("char_count") != len(expected_text):
            _error(f"{tid}: char_count mismatch", errors)

    # Normalization before LLM: state history + file mtimes
    if STATE_PATH.exists():
        state = _load_json(STATE_PATH)
        history = state.get("history", [])
        try:
            norm_idx = history.index(PipelineStage.TICKETS_NORMALIZED.value)
            pred_idx = history.index(PipelineStage.TRIAGE_PREDICTED.value)
            if pred_idx <= norm_idx:
                _error(
                    "pipeline_state: TRIAGE_PREDICTED before TICKETS_NORMALIZED",
                    errors,
                )
        except ValueError:
            _error("pipeline_state missing required stage history", errors)
    if NORMALIZED_PATH.stat().st_mtime > PREDICTIONS_PATH.stat().st_mtime:
        _error(
            "normalized_tickets.json is newer than triage_predictions.json",
            errors,
        )

    if LLM_LOG_PATH.exists():
        if NORMALIZED_PATH.stat().st_mtime > LLM_LOG_PATH.stat().st_mtime:
            _error("llm_calls.jsonl written before normalization", errors)

    # One prediction per ticket
    pred_ids = [p.get("ticket_id") for p in predictions if isinstance(p, dict)]
    if set(pred_ids) != ticket_ids or len(pred_ids) != len(ticket_ids):
        _error("Predictions must include exactly one entry per ticket", errors)

    max_words = config["reply_style"]["max_words"]
    allowed_cats = set(config["allowed_categories"])
    allowed_pri = set(config["allowed_priorities"])
    rules = config["routing_rules"]

    pred_by_id = {p["ticket_id"]: p for p in predictions if isinstance(p, dict)}
    override_map = {o["ticket_id"]: o for o in overrides if isinstance(o, dict)}

    for tid, pred in pred_by_id.items():
        cat = pred.get("category")
        pri = pred.get("priority")
        if cat not in allowed_cats:
            _error(f"{tid}: invalid category {cat}", errors)
        if pri not in allowed_pri:
            _error(f"{tid}: invalid priority {pri}", errors)
        expected_route = rules.get(cat)
        if pred.get("route_to") != expected_route:
            _error(
                f"{tid}: route_to {pred.get('route_to')} != config {expected_route}",
                errors,
            )
        reply = pred.get("suggested_reply", "")
        if word_count(str(reply)) > max_words:
            _error(f"{tid}: suggested_reply exceeds max_words", errors)

    # Overrides validity and application in final queue
    final_by_id = {q["ticket_id"]: q for q in final_queue if isinstance(q, dict)}
    if set(final_by_id) != ticket_ids:
        _error("final_queue.json ticket set mismatch", errors)

    for oid, ov in override_map.items():
        for field in (
            "old_category",
            "new_category",
            "old_priority",
            "new_priority",
        ):
            if field not in ov:
                _error(f"Override {oid}: missing {field}", errors)
        if ov["new_category"] not in allowed_cats:
            _error(f"Override {oid}: invalid new_category", errors)
        if ov["new_priority"] not in allowed_pri:
            _error(f"Override {oid}: invalid new_priority", errors)
        if oid in pred_by_id:
            if ov["old_category"] != pred_by_id[oid]["category"]:
                _error(f"Override {oid}: old_category mismatch", errors)
            if ov["old_priority"] != pred_by_id[oid]["priority"]:
                _error(f"Override {oid}: old_priority mismatch", errors)

    for tid, entry in final_by_id.items():
        reviewed_cat = pred_by_id[tid]["category"]
        reviewed_pri = pred_by_id[tid]["priority"]
        if tid in override_map:
            reviewed_cat = override_map[tid]["new_category"]
            reviewed_pri = override_map[tid]["new_priority"]
        if entry["final_category"] != reviewed_cat:
            _error(f"{tid}: final_category not applied from review", errors)
        if entry["final_priority"] != reviewed_pri:
            _error(f"{tid}: final_priority not applied from review", errors)
        if entry["final_route_to"] != rules.get(reviewed_cat):
            _error(f"{tid}: final_route_to mismatch after review", errors)
        was = entry.get("was_overridden", False)
        if was != (tid in override_map):
            _error(f"{tid}: was_overridden flag incorrect", errors)

    # Summary content
    if f"**Total tickets:** {len(ticket_ids)}" not in summary_text:
        _error("queue_summary.md missing total ticket count", errors)

    if ESCALATIONS_PATH.exists():
        escalations = _load_json(ESCALATIONS_PATH)
        if isinstance(escalations, list):
            for esc in escalations:
                tid = esc.get("ticket_id")
                p = pred_by_id.get(tid, {})
                conf = float(p.get("confidence", 1.0))
                reasons = esc.get("escalation_reasons", [])
                if p.get("category") == "other" and not any(
                    "other" in r for r in reasons
                ):
                    _error(f"{tid}: escalation missing other reason", errors)
                if conf < 0.60 and not any("confidence" in r for r in reasons):
                    _error(f"{tid}: escalation missing low confidence reason", errors)


def _load_config_safe(path: Path, errors: list[str]) -> dict | None:
    try:
        return load_config(path)
    except (json.JSONDecodeError, OSError) as exc:
        _error(f"Invalid config: {exc}", errors)
        return None


def main() -> int:
    errors: list[str] = []
    validate(errors)
    if errors:
        print("VALIDATION FAILED", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print("VALIDATION PASSED")
    print(f"  Artifacts OK under {ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
