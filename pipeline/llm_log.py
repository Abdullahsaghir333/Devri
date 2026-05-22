"""Append-only LLM call logging."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def log_llm_call(
    log_path: Path,
    *,
    stage: str,
    provider: str,
    model: str,
    prompt: str,
    input_artifacts: list[str],
    output_artifact: str,
) -> None:
    entry: dict[str, Any] = {
        "stage": stage,
        "timestamp": _utc_now_iso(),
        "provider": provider,
        "model": model,
        "prompt_hash": prompt_hash(prompt),
        "input_artifacts": input_artifacts,
        "output_artifact": output_artifact,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
