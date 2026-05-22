#!/usr/bin/env python3
"""Run the support ticket triage pipeline end-to-end."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.config import load_config
from pipeline.env import load_dotenv
from pipeline.normalize import normalize_tickets
from pipeline.paths import (
    CONFIG_PATH,
    ESCALATIONS_PATH,
    FINAL_QUEUE_PATH,
    LLM_LOG_PATH,
    NORMALIZED_PATH,
    OVERRIDES_PATH,
    PREDICTIONS_PATH,
    RECOVERY_LOG_PATH,
    ROOT,
    SUMMARY_PATH,
    TICKETS_PATH,
)
from pipeline.queue import (
    build_escalations,
    build_final_queue,
    save_json,
    write_queue_summary,
)
from pipeline.review import apply_overrides, collect_overrides, save_overrides
from pipeline.stages import PipelineStage, StageTracker
from pipeline.triage import predict_triage


def _reset_generated() -> None:
    for path in (
        NORMALIZED_PATH,
        PREDICTIONS_PATH,
        OVERRIDES_PATH,
        FINAL_QUEUE_PATH,
        SUMMARY_PATH,
        ESCALATIONS_PATH,
        LLM_LOG_PATH,
        RECOVERY_LOG_PATH,
        ROOT / "pipeline_state.json",
    ):
        if path.exists():
            path.unlink()


def run(*, non_interactive: bool = False, reset: bool = True) -> int:
    load_dotenv(override=True)
    if reset:
        _reset_generated()

    tracker = StageTracker()

    # INIT -> INPUTS_LOADED
    if not TICKETS_PATH.exists() or not CONFIG_PATH.exists():
        print("Missing tickets.json or triage_config.json", file=sys.stderr)
        return 1
    tickets = json.loads(TICKETS_PATH.read_text(encoding="utf-8"))
    config = load_config(CONFIG_PATH)
    tracker.advance_to(PipelineStage.INPUTS_LOADED)

    # INPUTS_LOADED -> TICKETS_NORMALIZED (deterministic, before any LLM)
    normalized = normalize_tickets(tickets, NORMALIZED_PATH)
    tracker.advance_to(PipelineStage.TICKETS_NORMALIZED)

    # TICKETS_NORMALIZED -> TRIAGE_PREDICTED
    predictions = predict_triage(normalized, config)
    tracker.advance_to(PipelineStage.TRIAGE_PREDICTED)

    # TRIAGE_PREDICTED -> HUMAN_REVIEW_COMPLETE
    overrides = collect_overrides(
        predictions, config, non_interactive=non_interactive
    )
    save_overrides(overrides, OVERRIDES_PATH)
    reviewed = apply_overrides(predictions, overrides, config)
    tracker.advance_to(PipelineStage.HUMAN_REVIEW_COMPLETE)

    # HUMAN_REVIEW_COMPLETE -> FINAL_QUEUE_GENERATED
    final_queue = build_final_queue(reviewed, overrides)
    save_json(final_queue, FINAL_QUEUE_PATH)
    write_queue_summary(final_queue, SUMMARY_PATH)
    escalations = build_escalations(reviewed)
    save_json(escalations, ESCALATIONS_PATH)
    tracker.advance_to(PipelineStage.FINAL_QUEUE_GENERATED)

    print("\nPipeline complete. Artifacts written:")
    for path in (
        NORMALIZED_PATH,
        PREDICTIONS_PATH,
        OVERRIDES_PATH,
        FINAL_QUEUE_PATH,
        SUMMARY_PATH,
        ESCALATIONS_PATH,
    ):
        print(f"  - {path.name}")
    if LLM_LOG_PATH.exists():
        print(f"  - {LLM_LOG_PATH.name}")
    print(f"\nStage: {tracker.stage.value}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Support ticket triage pipeline")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Skip interactive review (no overrides)",
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Do not delete existing generated artifacts before run",
    )
    args = parser.parse_args()
    return run(non_interactive=args.non_interactive, reset=not args.no_reset)


if __name__ == "__main__":
    raise SystemExit(main())
