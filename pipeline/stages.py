"""Pipeline stage tracking and enforcement."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from pipeline.paths import STATE_PATH


class PipelineStage(str, Enum):
    INIT = "INIT"
    INPUTS_LOADED = "INPUTS_LOADED"
    TICKETS_NORMALIZED = "TICKETS_NORMALIZED"
    TRIAGE_PREDICTED = "TRIAGE_PREDICTED"
    HUMAN_REVIEW_COMPLETE = "HUMAN_REVIEW_COMPLETE"
    FINAL_QUEUE_GENERATED = "FINAL_QUEUE_GENERATED"
    VALIDATION_COMPLETE = "VALIDATION_COMPLETE"
    RESULTS_FINALISED = "RESULTS_FINALISED"


_STAGE_ORDER = list(PipelineStage)


class StageTracker:
    def __init__(self, state_path: Path = STATE_PATH) -> None:
        self.state_path = state_path
        self._stage = PipelineStage.INIT
        self._history: list[str] = []

    @property
    def stage(self) -> PipelineStage:
        return self._stage

    def advance_to(self, target: PipelineStage) -> None:
        current_idx = _STAGE_ORDER.index(self._stage)
        target_idx = _STAGE_ORDER.index(target)
        if target_idx != current_idx + 1:
            raise RuntimeError(
                f"Invalid stage transition: {self._stage.value} -> {target.value}"
            )
        self._stage = target
        self._history.append(target.value)
        self._persist()

    def require_at_least(self, minimum: PipelineStage) -> None:
        if _STAGE_ORDER.index(self._stage) < _STAGE_ORDER.index(minimum):
            raise RuntimeError(
                f"Pipeline at {self._stage.value}, requires at least {minimum.value}"
            )

    def _persist(self) -> None:
        payload = {
            "current_stage": self._stage.value,
            "history": self._history,
        }
        self.state_path.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def load_existing(self) -> None:
        if not self.state_path.exists():
            return
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self._stage = PipelineStage(data["current_stage"])
        self._history = data.get("history", [])
